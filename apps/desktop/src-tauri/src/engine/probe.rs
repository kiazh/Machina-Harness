//! Bounded, read-only native feasibility check. No agent/tool invocation surface.
use super::framing::FrameReader;
use serde_json::{json, Value};
use std::io::{BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

pub(super) struct OwnedEngine {
    pub child: Child,
    #[cfg(windows)]
    job: Option<std::os::windows::io::OwnedHandle>,
}

/// A packaged desktop client starts the Python gateway directly. It never
/// shells through the terminal client, and a checkout is only needed by the
/// development launcher.
fn development_launch(
    python: Option<std::ffi::OsString>,
    root: Option<PathBuf>,
) -> Result<(std::ffi::OsString, Option<PathBuf>), String> {
    if !cfg!(any(debug_assertions, feature = "dev-engine")) {
        return Err("No managed engine is installed; this build does not allow a development interpreter".into());
    }
    let python = python.unwrap_or_else(|| {
        if cfg!(windows) { "python".into() } else { "python3".into() }
    });
    if let Some(root) = root {
        if !root.join("machina/desktop/bootstrap.py").is_file() {
            return Err("The configured development checkout does not contain the desktop engine".into());
        }
        return Ok((python, Some(root)));
    }
    Ok((python, None))
}

impl Drop for OwnedEngine {
    fn drop(&mut self) {
        self.child.stdin.take();
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) { break; }
            std::thread::sleep(Duration::from_millis(20));
        }
        #[cfg(windows)]
        drop(self.job.take());
        #[cfg(unix)]
        unsafe { libc::kill(-(self.child.id() as i32), libc::SIGKILL); }
        if !matches!(self.child.try_wait(), Ok(Some(_))) { let _ = self.child.kill(); }
        let _ = self.child.wait();
    }
}

#[cfg(windows)]
fn own_process(child: &mut Child) -> Result<windows_sys::Win32::Foundation::HANDLE, String> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::JobObjects::*;
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() { return Err("Could not create engine process ownership".into()); }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits as *const _ as *const _, std::mem::size_of_val(&limits) as u32) == 0
            || AssignProcessToJobObject(job, child.as_raw_handle()) == 0 {
            CloseHandle(job);
            return Err("Could not own engine process tree".into());
        }
        Ok(job)
    }
}

pub(super) fn spawn() -> Result<(OwnedEngine, String), String> {
    let (python, root) = development_launch(
        std::env::var_os("MACHINA_PYTHON"),
        std::env::var_os("MACHINA_REPO").map(PathBuf::from),
    )?;
    let mut token_bytes = [0_u8; 32];
    getrandom::fill(&mut token_bytes).map_err(|_| "Could not create engine credentials")?;
    let token: String = token_bytes.iter().map(|v| format!("{v:02x}")).collect();
    let mut command = Command::new(python);
    command.args(["-c", "import os,runpy; gate=os.read(0,1); assert gate==bytes([0]); runpy.run_module('machina.desktop',run_name='__main__')"])
        .env("MACHINA_GATEWAY_TOKEN", &token)
        .env("PYTHONUNBUFFERED", "1").env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    if let Some(root) = root { command.current_dir(root); }
    #[cfg(windows)]
    { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    #[cfg(unix)]
    { use std::os::unix::process::CommandExt; command.process_group(0); }
    let mut child = command.spawn().map_err(|_| "Could not launch the configured Python engine")?;
    #[cfg(windows)]
    let job = match own_process(&mut child) {
        Ok(job) => job,
        Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error); }
    };
    #[cfg(windows)]
    let job = {
        use std::os::windows::io::FromRawHandle;
        Some(unsafe { std::os::windows::io::OwnedHandle::from_raw_handle(job) })
    };
    let mut engine = OwnedEngine { child, #[cfg(windows)] job };
    engine.child.stdin.as_mut().ok_or("Missing engine input channel")?.write_all(&[0])
        .map_err(|_| "Could not admit the owned engine")?;
    Ok((engine, token))
}

pub fn run() -> Result<Value, String> {
    let (mut engine, token) = spawn()?;
    let stdout = engine.child.stdout.take().ok_or("Missing engine output channel")?;
    let mut stderr = engine.child.stderr.take().ok_or("Missing engine diagnostic channel")?;
    // Drain diagnostics without exposing credentials/project data to the renderer.
    std::thread::spawn(move || { let mut buffer = [0; 4096]; while matches!(stderr.read(&mut buffer), Ok(n) if n > 0) {} });
    let (send, receive) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let mut frames = FrameReader::new(BufReader::new(stdout));
        loop {
            let frame = frames.next_frame();
            let stop = !matches!(&frame, Ok(Some(_)));
            if send.send(frame).is_err() || stop { break; }
        }
    });
    let stdin = engine.child.stdin.as_mut().ok_or("Missing engine input channel")?;
    writeln!(stdin, "{}", json!({"jsonrpc":"2.0","id":1,"method":"runtime.capabilities","params":{"token":token}}))
        .map_err(|_| "Could not write engine handshake")?;
    stdin.flush().map_err(|_| "Could not flush engine handshake")?;
    let deadline = Instant::now() + Duration::from_secs(30);
    let response = loop {
        let frame = receive.recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .map_err(|_| "Engine did not answer the capability handshake within 30 seconds")?
            .map_err(String::from)?.ok_or("Engine exited before its capability handshake")?;
        if frame.get("id") == Some(&json!(1)) { break frame; }
    };
    if response.get("error").is_some() { return Err("Engine rejected the capability handshake".into()); }
    let result = response.get("result").cloned().ok_or("Engine omitted its capability result")?;
    if let Some(stdin) = engine.child.stdin.as_mut() {
        let _ = writeln!(stdin, "{}", json!({"jsonrpc":"2.0","id":2,"method":"shutdown","params":{"token":token}}));
        let _ = stdin.flush();
    }
    drop(engine);
    Ok(result)
}

#[cfg(test)]
mod tests {

    #[test]
    fn development_launch_uses_path_python_without_a_checkout() {
        let (python, root) = super::development_launch(None, None).unwrap();
        assert_eq!(python, if cfg!(windows) { "python" } else { "python3" });
        assert!(root.is_none());
    }

    #[test]
    fn real_engine_handshake_when_development_interpreter_is_supplied() {
        if std::env::var_os("MACHINA_PYTHON").is_none() { return; }
        let result = super::run().expect("real desktop engine handshake");
        assert_eq!(result["protocolVersion"], 2);
        assert!(result["methods"].as_array().unwrap().iter().any(|method| method == "workspace.open"));
    }
}
