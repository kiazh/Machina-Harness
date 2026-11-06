use super::framing::{FrameReader, MAX_FRAME_BYTES};
use super::budget::{ByteBudget, ByteLease};
use super::events::{EventDispatcher, EventSink};
use super::pending::PendingRequests;
use super::probe::{spawn, OwnedEngine};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::io::{BufReader, Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex, OnceLock};
use std::time::Duration;

struct OutboundFrame { bytes: Vec<u8>, _lease: ByteLease }
struct Shared {
    pending: PendingRequests,
    writer: Mutex<Option<mpsc::SyncSender<OutboundFrame>>>,
    budget: ByteBudget,
    connected: AtomicBool,
}
impl Shared {
    fn disconnect(&self) {
        self.connected.store(false, Ordering::Release);
        self.pending.fail_all("Engine disconnected; the outcome of unfinished work is unknown");
        if let Ok(mut writer) = self.writer.lock() { writer.take(); }
    }
}

pub struct EngineSession {
    pub generation: u64,
    shared: Arc<Shared>,
    child: Mutex<Option<OwnedEngine>>,
    token: String,
}

impl EngineSession {
    pub fn start(generation: u64, events: EventSink) -> Result<Self, String> {
        let (mut engine, token) = spawn()?;
        let mut stdin = engine.child.stdin.take().ok_or("Missing engine input channel")?;
        let stdout = engine.child.stdout.take().ok_or("Missing engine output channel")?;
        let mut stderr = engine.child.stderr.take().ok_or("Missing engine diagnostic channel")?;
        let (writer, queue) = mpsc::sync_channel::<OutboundFrame>(64);
        let shared = Arc::new(Shared { pending: PendingRequests::default(), writer: Mutex::new(Some(writer)), budget: ByteBudget::new(MAX_FRAME_BYTES), connected: AtomicBool::new(true) });
        let weak = Arc::downgrade(&shared);
        std::thread::spawn(move || {
            while let Ok(frame) = queue.recv() {
                if stdin.write_all(&frame.bytes).and_then(|_| stdin.flush()).is_err() {
                    if let Some(shared) = weak.upgrade() { shared.disconnect(); }
                    break;
                }
            }
        });
        std::thread::spawn(move || { let mut buffer = [0; 4096]; while matches!(stderr.read(&mut buffer), Ok(n) if n > 0) {} });
        let receiving = shared.clone();
        let events = EventDispatcher::new(events);
        std::thread::spawn(move || {
            let mut reader = FrameReader::new(BufReader::new(stdout));
            loop {
                match reader.next_frame() {
                    Ok(Some(frame)) => {
                        if frame.get("method").and_then(Value::as_str) == Some("event") {
                            if let Some(payload) = frame.get("params").filter(|value| value.is_object()) {
                                if events.push(payload.clone()).is_err() { receiving.disconnect(); break; }
                            }
                        } else { receiving.pending.resolve(&frame); }
                    }
                    _ => { receiving.disconnect(); break; }
                }
            }
        });
        Ok(Self { generation, shared, child: Mutex::new(Some(engine)), token })
    }

    pub fn connected(&self) -> bool { self.shared.connected.load(Ordering::Acquire) }

    pub fn request(&self, method: &str, mut params: Value) -> Result<Value, String> {
        static ALLOWED: OnceLock<HashSet<String>> = OnceLock::new();
        let allowed = ALLOWED.get_or_init(|| serde_json::from_str(include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../packages/contracts/src/rpc-methods.json"))).expect("generated RPC registry"));
        if !allowed.contains(method) { return Err("Unsupported engine method".into()); }
        if !self.connected() { return Err("Engine is not connected".into()); }
        params.as_object_mut().ok_or("Engine parameters must be an object")?.insert("token".into(), json!(self.token));
        let (id, reply) = self.shared.pending.reserve()?;
        let mut frame = match serde_json::to_vec(&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})) {
            Ok(frame) => frame,
            Err(_) => { self.shared.pending.remove(id); return Err("Could not encode engine request".into()); }
        };
        frame.push(b'\n');
        if frame.len() > MAX_FRAME_BYTES { self.shared.pending.remove(id); return Err("Engine request exceeds size limit".into()); }
        let lease = match self.shared.budget.reserve(frame.len()) {
            Ok(lease) => lease,
            Err(error) => { self.shared.pending.remove(id); return Err(error); }
        };
        let frame = OutboundFrame { bytes: frame, _lease: lease };
        let sent = self.shared.writer.lock().ok().and_then(|writer| writer.as_ref().map(|writer| writer.try_send(frame).is_ok())).unwrap_or(false);
        if !sent { self.shared.pending.remove(id); return Err("Engine request queue is unavailable or full".into()); }
        let result = reply.recv_timeout(Duration::from_secs(60));
        self.shared.pending.remove(id);
        result.map_err(|_| "Engine did not answer within 60 seconds; do not automatically repeat this operation".to_owned())?
    }

    pub fn stop(&self) {
        if let Ok(mut writer) = self.shared.writer.lock() {
            if let Some(writer) = writer.take() {
                let mut frame = serde_json::to_vec(&json!({"jsonrpc":"2.0","id":0,"method":"shutdown","params":{"token":self.token}})).unwrap_or_default();
                frame.push(b'\n');
                if let Ok(lease) = self.shared.budget.reserve(frame.len()) { let _ = writer.try_send(OutboundFrame { bytes: frame, _lease: lease }); }
            }
        }
        self.shared.disconnect();
        if let Ok(mut child) = self.child.lock() { child.take(); }
    }
}
impl Drop for EngineSession { fn drop(&mut self) { self.stop(); } }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn persistent_real_engine_roundtrip_and_shutdown() {
        if std::env::var_os("MACHINA_PYTHON").is_none() { return; }
        let engine = EngineSession::start(1, Arc::new(|_| {})).unwrap();
        assert_eq!(engine.request("runtime.capabilities", json!({})).unwrap()["protocolVersion"], 2);
        assert!(engine.request("unknown.untrusted", json!({})).is_err());
        engine.stop();
        assert!(!engine.connected());
        assert!(engine.request("runtime.capabilities", json!({})).is_err());
    }

    #[test]
    fn real_workspace_initialize_list_and_read_without_model_calls() {
        if std::env::var_os("MACHINA_DESKTOP_WORKSPACE_SMOKE").is_none() { return; }
        let engine = EngineSession::start(1, Arc::new(|_| {})).unwrap();
        let initialized = engine.request("initialize", json!({"protocolVersion":2})).unwrap();
        let root = initialized.get("cwdFull").or_else(|| initialized.get("cwd")).and_then(Value::as_str).expect("engine cwd");
        let workspace = engine.request("runtime.selectWorkspace", json!({"root":root,"trust":true})).unwrap();
        let binding = json!({"workspaceId":workspace["workspaceId"],"epoch":workspace["epoch"],"clientId":workspace["clientId"]});
        let mut listing = binding.clone(); listing["path"] = json!("");
        let result = engine.request("workspace.list", listing).unwrap();
        assert!(result["entries"].as_array().unwrap().iter().any(|entry| entry["name"] == "README.md"));
        let uri = tauri::Url::from_file_path(std::path::Path::new(root).join("README.md")).unwrap().to_string();
        let mut reading = binding.clone(); reading["uri"] = json!(uri);
        let read = engine.request("workspace.read", reading).unwrap();
        assert!(read["text"].as_str().unwrap().contains("Machina"));
        let changes = engine.request("workspace.changes", binding.clone()).unwrap();
        assert_eq!(changes["state"], "ready", "real Git inspection: {changes}");
        assert_eq!(changes["workspaceId"], workspace["workspaceId"]);
        let mut diff = binding.clone(); diff["path"] = json!("README.md"); diff["target"] = json!("working");
        let reviewed = engine.request("workspace.diff", diff).unwrap();
        assert_eq!(reviewed["path"], "README.md");
        assert_eq!(reviewed["target"], "working");
        assert_eq!(reviewed["binary"], false);
        engine.stop();
    }

    #[test]
    fn real_python_language_completion_and_diagnostics_against_unsaved_mirror() {
        if std::env::var_os("MACHINA_DESKTOP_LSP_SMOKE").is_none() { return; }
        let engine = EngineSession::start(1, Arc::new(|_| {})).unwrap();
        let initialized = engine.request("initialize", json!({"protocolVersion":2})).unwrap();
        let root = initialized.get("cwdFull").and_then(Value::as_str).unwrap();
        let workspace = engine.request("workspace.open", json!({"root":root,"trust":true})).unwrap();
        let uri = tauri::Url::from_file_path(std::path::Path::new(root).join("machina/__init__.py")).unwrap().to_string();
        let binding = json!({"workspaceId":workspace["workspaceId"],"epoch":workspace["epoch"],"clientId":workspace["clientId"],"uri":uri});
        let mut opening = binding.clone();
        opening["text"] = json!("import pathlib\npathlib.Pa"); opening["version"] = json!(0); opening["languageId"] = json!("python");
        let opened = engine.request("workspace.documentOpen", opening).unwrap();
        assert_eq!(opened["acceptedVersion"], 0);
        let deadline = std::time::Instant::now() + Duration::from_secs(20);
        loop {
            let mut request = binding.clone(); request["operation"] = json!("completion"); request["version"] = json!(0); request["position"] = json!({"line":1,"character":10});
            let result = engine.request("workspace.intelligence", request).unwrap();
            if result.pointer("/result/items").and_then(Value::as_array).is_some_and(|items| items.iter().any(|item| item["label"] == "Path")) { break; }
            assert!(std::time::Instant::now() < deadline, "Python completion unavailable: state={}, freshness={}", result["state"], result["freshness"]);
            std::thread::sleep(Duration::from_millis(150));
        }
        let deadline = std::time::Instant::now() + Duration::from_secs(20);
        loop {
            let mut request = binding.clone(); request["operation"] = json!("diagnostics"); request["version"] = json!(0);
            let result = engine.request("workspace.intelligence", request).unwrap();
            let errors = result.pointer("/result/diagnostics").and_then(Value::as_array);
            if result["freshness"] == "versioned" && errors.is_some_and(|rows| rows.iter().any(|row| row["severity"] == 1)) {
                assert_eq!(result["documentVersion"], 0);
                assert_eq!(result["source"], "editor-buffer");
                break;
            }
            assert!(std::time::Instant::now() < deadline, "Python diagnostics unavailable: state={}, freshness={}", result["state"], result["freshness"]);
            std::thread::sleep(Duration::from_millis(150));
        }
        engine.stop();
    }
}
