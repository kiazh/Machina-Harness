//! User-opened interactive shells. Independent from agent command jobs.
use portable_pty::{native_pty_system, Child, ChildKiller, CommandBuilder, MasterPty, PtySize};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

const OUTPUT_LIMIT: usize = 256 * 1024;
const READ_LIMIT: usize = 64 * 1024;
const SESSION_LIMIT: usize = 8;

#[derive(Default)]
struct OutputBuffer {
    bytes: VecDeque<u8>,
    truncated: bool,
}

impl OutputBuffer {
    fn push(&mut self, bytes: &[u8]) {
        let overflow = self.bytes.len().saturating_add(bytes.len()).saturating_sub(OUTPUT_LIMIT);
        if overflow > 0 {
            self.truncated = true;
            self.bytes.drain(..overflow.min(self.bytes.len()));
        }
        let bytes = &bytes[bytes.len().saturating_sub(OUTPUT_LIMIT)..];
        self.bytes.extend(bytes);
    }

    fn drain(&mut self) -> (Vec<u8>, bool) {
        (self.bytes.drain(..self.bytes.len().min(READ_LIMIT)).collect(), std::mem::take(&mut self.truncated))
    }
}

fn workspace_directory(cwd: &str) -> Result<PathBuf, String> {
    let path = Path::new(cwd);
    if !path.is_absolute() { return Err("Choose an absolute workspace folder before opening a terminal".into()); }
    let path = path.canonicalize().map_err(|_| "The terminal workspace folder is unavailable")?;
    if !path.is_dir() { return Err("The terminal workspace must be a folder".into()); }
    // ConPTY/PowerShell should receive a normal Win32 cwd, not Rust's extended
    // canonical prefix (which PowerShell exposes as a provider-qualified path).
    #[cfg(windows)]
    let path = {
        let text = path.to_string_lossy();
        if let Some(unc) = text.strip_prefix("\\\\?\\UNC\\") { PathBuf::from(format!("\\\\{unc}")) }
        else if let Some(local) = text.strip_prefix("\\\\?\\") { PathBuf::from(local) }
        else { path.clone() }
    };
    Ok(path)
}

fn dimensions(cols: u16, rows: u16) -> Result<PtySize, String> {
    if !(2..=500).contains(&cols) || !(1..=200).contains(&rows) { return Err("Terminal dimensions are out of range".into()); }
    Ok(PtySize { cols, rows, pixel_width: 0, pixel_height: 0 })
}

fn shell_command(cwd: &Path) -> (CommandBuilder, String) {
    #[cfg(windows)]
    let (mut command, label) = {
        // Use the OS PowerShell by absolute path; avoid invoking a shell to find a shell.
        let root = std::env::var_os("SystemRoot").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("C:\\Windows"));
        let mut command = CommandBuilder::new(root.join("System32/WindowsPowerShell/v1.0/powershell.exe"));
        command.arg("-NoLogo");
        (command, "PowerShell".to_owned())
    };
    #[cfg(unix)]
    let (mut command, label) = {
        let shell = std::env::var_os("SHELL").map(PathBuf::from)
            .filter(|path| path.is_absolute() && path.is_file()).unwrap_or_else(|| PathBuf::from("/bin/zsh"));
        let label = shell.file_name().unwrap_or_default().to_string_lossy().into_owned();
        let mut command = CommandBuilder::new(shell);
        command.arg("-l");
        (command, label)
    };
    command.cwd(cwd);
    command.env("TERM", "xterm-256color");
    command.env("COLORTERM", "truecolor");
    (command, label)
}

#[derive(Default)]
struct Shared {
    output: Mutex<OutputBuffer>,
    exited: AtomicBool,
    reader_done: AtomicBool,
    exit_code: Mutex<Option<u32>>,
    error: Mutex<Option<String>>,
}

struct ProcessTree {
    #[cfg(windows)]
    job: Mutex<Option<std::os::windows::io::OwnedHandle>>,
    #[cfg(unix)]
    pid: Option<u32>,
}

impl ProcessTree {
    fn own(child: &dyn Child) -> Result<Self, String> {
        #[cfg(windows)]
        {
            use std::os::windows::io::FromRawHandle;
            use windows_sys::Win32::Foundation::CloseHandle;
            use windows_sys::Win32::System::JobObjects::*;
            let process = child.as_raw_handle().ok_or("Missing terminal process handle")?;
            unsafe {
                let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if job.is_null() { return Err("Could not create terminal process ownership".into()); }
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits as *const _ as *const _, std::mem::size_of_val(&limits) as u32) == 0
                    || AssignProcessToJobObject(job, process) == 0 {
                    CloseHandle(job);
                    return Err("Could not own the terminal process tree".into());
                }
                Ok(Self { job: Mutex::new(Some(std::os::windows::io::OwnedHandle::from_raw_handle(job))) })
            }
        }
        #[cfg(unix)]
        { Ok(Self { pid: child.process_id() }) }
    }

    fn terminate(&self) {
        #[cfg(windows)]
        if let Ok(mut job) = self.job.lock() { job.take(); }
        #[cfg(unix)]
        if let Some(pid) = self.pid { unsafe { libc::kill(-(pid as i32), libc::SIGKILL); } }
    }
}

struct TerminalSession {
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    killer: Mutex<Box<dyn ChildKiller + Send + Sync>>,
    tree: Arc<ProcessTree>,
    shared: Arc<Shared>,
    closed: AtomicBool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TerminalOpened { id: u64, shell: String, cwd: String }

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TerminalRead {
    data: Vec<u8>, truncated: bool, exited: bool, exit_code: Option<u32>, error: Option<String>,
}

impl TerminalSession {
    fn start(cwd: &Path, cols: u16, rows: u16) -> Result<(Arc<Self>, String), String> {
        let pair = native_pty_system().openpty(dimensions(cols, rows)?).map_err(|error| format!("Could not open terminal: {error}"))?;
        let mut reader = pair.master.try_clone_reader().map_err(|error| error.to_string())?;
        let writer = pair.master.take_writer().map_err(|error| error.to_string())?;
        let (command, label) = shell_command(cwd);
        let mut child = pair.slave.spawn_command(command).map_err(|error| format!("Could not launch shell: {error}"))?;
        drop(pair.slave);
        let tree = match ProcessTree::own(child.as_ref()) {
            Ok(tree) => Arc::new(tree),
            Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error); }
        };
        let shared = Arc::new(Shared::default());
        let session = Arc::new(Self {
            master: Mutex::new(Some(pair.master)), writer: Mutex::new(Some(writer)),
            killer: Mutex::new(child.clone_killer()), tree: tree.clone(), shared: shared.clone(), closed: AtomicBool::new(false),
        });
        let reading = shared.clone();
        std::thread::spawn(move || {
            let mut bytes = [0; 8192];
            loop {
                match reader.read(&mut bytes) {
                    Ok(0) => break,
                    Ok(count) => if let Ok(mut output) = reading.output.lock() { output.push(&bytes[..count]); },
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                    // Unix PTYs commonly report EIO when the slave closes.
                    Err(_) => break,
                }
            }
            reading.reader_done.store(true, Ordering::Release);
        });
        let waiting_session = Arc::downgrade(&session);
        std::thread::spawn(move || {
            match child.wait() {
                Ok(status) => if let Ok(mut exit_code) = shared.exit_code.lock() { *exit_code = Some(status.exit_code()); },
                Err(error) => if let Ok(mut failure) = shared.error.lock() { *failure = Some(format!("Shell wait failed: {error}")); },
            }
            tree.terminate();
            shared.exited.store(true, Ordering::Release);
            // Windows keeps the ConPTY output pipe open while the master is
            // retained, even after the shell exits. Release it so the reader
            // can drain its tail and publish the final exit state.
            if let Some(session) = waiting_session.upgrade() { session.stop(); }
        });
        Ok((session, label))
    }

    fn write(&self, data: &str) -> Result<(), String> {
        if self.closed.load(Ordering::Acquire) || self.shared.exited.load(Ordering::Acquire) { return Err("This terminal has exited".into()); }
        if data.len() > READ_LIMIT { return Err("Terminal input exceeds 64 KiB; paste smaller chunks".into()); }
        let mut writer = self.writer.lock().map_err(|_| "Terminal input is unavailable")?;
        let writer = writer.as_mut().ok_or("This terminal is closed")?;
        writer.write_all(data.as_bytes()).and_then(|_| writer.flush()).map_err(|error| format!("Could not write terminal input: {error}"))
    }

    fn resize(&self, cols: u16, rows: u16) -> Result<(), String> {
        let size = dimensions(cols, rows)?;
        self.master.lock().map_err(|_| "Terminal is unavailable")?.as_ref().ok_or("This terminal is closed")?
            .resize(size).map_err(|error| format!("Could not resize terminal: {error}"))
    }

    fn read(&self) -> Result<TerminalRead, String> {
        let mut output = self.shared.output.lock().map_err(|_| "Terminal output is unavailable")?;
        let (data, truncated) = output.drain();
        let exited = self.shared.exited.load(Ordering::Acquire) && self.shared.reader_done.load(Ordering::Acquire) && output.bytes.is_empty();
        Ok(TerminalRead { data, truncated, exited,
            exit_code: *self.shared.exit_code.lock().map_err(|_| "Terminal status is unavailable")?,
            error: self.shared.error.lock().map_err(|_| "Terminal status is unavailable")?.clone(),
        })
    }

    fn stop(&self) {
        if self.closed.swap(true, Ordering::AcqRel) { return; }
        // End foreground jobs before the shell on Unix; their process groups can differ.
        #[cfg(unix)]
        if let Ok(master) = self.master.lock() {
            if let Some(group) = master.as_ref().and_then(|pty| pty.process_group_leader()).filter(|id| *id > 0) {
                unsafe { libc::kill(-group, libc::SIGKILL); }
            }
        }
        self.tree.terminate();
        if let Ok(mut killer) = self.killer.lock() { let _ = killer.kill(); }
        if let Ok(mut writer) = self.writer.lock() { writer.take(); }
        if let Ok(mut master) = self.master.lock() { master.take(); }
    }
}

impl Drop for TerminalSession { fn drop(&mut self) { self.stop(); } }

#[derive(Default)]
struct HostState { sessions: Mutex<HashMap<u64, Arc<TerminalSession>>>, next: AtomicU64 }

impl HostState {
    fn open(&self, cwd: &str, cols: u16, rows: u16) -> Result<TerminalOpened, String> {
        let cwd = workspace_directory(cwd)?;
        let mut sessions = self.sessions.lock().map_err(|_| "Terminal host is unavailable")?;
        if sessions.len() >= SESSION_LIMIT { return Err("Close a terminal before opening another (limit: 8)".into()); }
        let (session, shell) = TerminalSession::start(&cwd, cols, rows)?;
        let id = self.next.fetch_add(1, Ordering::Relaxed) + 1;
        sessions.insert(id, session);
        Ok(TerminalOpened { id, shell, cwd: cwd.to_string_lossy().into_owned() })
    }
    fn session(&self, id: u64) -> Result<Arc<TerminalSession>, String> {
        self.sessions.lock().map_err(|_| "Terminal host is unavailable")?.get(&id).cloned().ok_or_else(|| "This terminal is closed".into())
    }
    fn close(&self, id: u64) -> Result<(), String> {
        let session = self.sessions.lock().map_err(|_| "Terminal host is unavailable")?.remove(&id);
        if let Some(session) = session { session.stop(); }
        Ok(())
    }
    fn close_all(&self) {
        let sessions = self.sessions.lock().map(|mut sessions| std::mem::take(&mut *sessions)).unwrap_or_default();
        for session in sessions.values() { session.stop(); }
    }
}

#[derive(Default)]
pub struct TerminalHost { inner: Arc<HostState> }
impl TerminalHost { pub fn close_all(&self) { self.inner.close_all(); } }
impl Drop for TerminalHost { fn drop(&mut self) { self.close_all(); } }

#[tauri::command]
pub async fn terminal_open(state: tauri::State<'_, TerminalHost>, cwd: String, cols: u16, rows: u16) -> Result<TerminalOpened, String> {
    let host = state.inner.clone();
    tauri::async_runtime::spawn_blocking(move || host.open(&cwd, cols, rows)).await.map_err(|_| "Terminal startup worker failed".to_owned())?
}
#[tauri::command]
pub fn terminal_read(state: tauri::State<'_, TerminalHost>, id: u64) -> Result<TerminalRead, String> { state.inner.session(id)?.read() }
#[tauri::command]
pub async fn terminal_write(state: tauri::State<'_, TerminalHost>, id: u64, data: String) -> Result<(), String> {
    let session = state.inner.session(id)?;
    tauri::async_runtime::spawn_blocking(move || session.write(&data)).await.map_err(|_| "Terminal input worker failed".to_owned())?
}
#[tauri::command]
pub async fn terminal_resize(state: tauri::State<'_, TerminalHost>, id: u64, cols: u16, rows: u16) -> Result<(), String> {
    let session = state.inner.session(id)?;
    tauri::async_runtime::spawn_blocking(move || session.resize(cols, rows)).await.map_err(|_| "Terminal resize worker failed".to_owned())?
}
#[tauri::command]
pub async fn terminal_close(state: tauri::State<'_, TerminalHost>, id: u64) -> Result<(), String> {
    let host = state.inner.clone();
    tauri::async_runtime::spawn_blocking(move || host.close(id)).await.map_err(|_| "Terminal shutdown worker failed".to_owned())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workspace_validation_rejects_relative_missing_and_file_paths() {
        assert!(workspace_directory(".").is_err());
        assert!(workspace_directory("").is_err());
        assert!(workspace_directory(&std::env::temp_dir().join("machina-terminal-missing-directory").to_string_lossy()).is_err());
        assert!(workspace_directory(concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml")).is_err());
        assert!(workspace_directory(env!("CARGO_MANIFEST_DIR")).unwrap().is_dir());
    }

    #[test]
    fn native_shell_accepts_input_uses_workspace_resizes_and_reports_exit() {
        let host = HostState::default();
        let opened = host.open(env!("CARGO_MANIFEST_DIR"), 80, 24).unwrap();
        let session = host.session(opened.id).unwrap();
        session.resize(101, 31).unwrap();
        assert_eq!(session.master.lock().unwrap().as_ref().unwrap().get_size().unwrap().cols, 101);
        #[cfg(windows)]
        let command = "[Console]::WriteLine('WORKSPACE=' + (Get-Location).Path); [Console]::WriteLine('INTERACTIVE_' + 'OK'); exit 7\r";
        #[cfg(unix)]
        let command = "printf 'WORKSPACE=%s\\n' \"$PWD\"; printf 'INTERACTIVE_%s\\n' OK; exit 7\r";
        let mut command_sent = false;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
        let mut output = Vec::new();
        loop {
            let read = session.read().unwrap();
            // PowerShell/ConPTY queries the cursor before showing its prompt.
            // The real xterm view replies automatically; emulate that terminal here.
            if read.data.windows(4).any(|bytes| bytes == b"\x1b[6n") {
                session.write("\x1b[1;1R").unwrap();
            }
            output.extend(read.data);
            if !command_sent {
                #[cfg(windows)]
                let ready = String::from_utf8_lossy(&output).contains("> ");
                #[cfg(unix)]
                let ready = true;
                if ready { session.write(command).unwrap(); command_sent = true; }
            }
            if read.exited { assert_eq!(read.exit_code, Some(7)); break; }
            assert!(std::time::Instant::now() < deadline, "shell did not exit: {}", String::from_utf8_lossy(&output));
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let text = String::from_utf8_lossy(&output);
        assert!(text.contains("INTERACTIVE_OK"), "{text}");
        assert!(text.contains("src-tauri"), "{text}");
        host.close(opened.id).unwrap();
        assert!(host.session(opened.id).is_err());
    }

    #[test]
    fn closing_a_tab_terminates_a_running_shell_and_releases_the_session() {
        let host = HostState::default();
        let opened = host.open(env!("CARGO_MANIFEST_DIR"), 80, 24).unwrap();
        let session = host.session(opened.id).unwrap();
        host.close(opened.id).unwrap();
        assert!(host.session(opened.id).is_err());
        assert!(session.write("should not run\r").is_err());
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !session.shared.exited.load(std::sync::atomic::Ordering::Acquire) {
            assert!(std::time::Instant::now() < deadline, "closed shell is still running");
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
    }

    #[test]
    fn slow_consumers_keep_only_recent_output_and_receive_a_truncation_notice() {
        let mut output = OutputBuffer::default();
        output.push(&vec![b'a'; OUTPUT_LIMIT]);
        output.push(b"newest");
        assert_eq!(output.bytes.len(), OUTPUT_LIMIT);
        let mut received = Vec::new();
        let (bytes, truncated) = output.drain();
        assert!(truncated);
        assert_eq!(bytes.len(), READ_LIMIT);
        received.extend(bytes);
        while !output.bytes.is_empty() {
            let (bytes, truncated) = output.drain();
            assert!(!truncated);
            assert!(bytes.len() <= READ_LIMIT);
            received.extend(bytes);
        }
        assert!(received.ends_with(b"newest"));
        assert_eq!(received.len(), OUTPUT_LIMIT);
    }

    #[test]
    fn output_preserves_raw_ansi_and_split_utf8_bytes() {
        let mut output = OutputBuffer::default();
        output.push(&[0x1b, b'[', b'3', b'1', b'm', 0xe2]);
        output.push(&[0x9c, 0x93]);
        assert_eq!(output.drain(), (b"\x1b[31m\xe2\x9c\x93".to_vec(), false));
        assert_eq!(output.drain(), (vec![], false));
    }
}
