mod framing;
mod budget;
mod events;
mod pending;
mod probe;
mod session;

use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU64, Ordering};
use serde_json::{json, Value};
use tauri::Emitter;

#[derive(Default)]
struct HostState {
    session: Mutex<Option<Arc<session::EngineSession>>>,
    generation: AtomicU64,
}

#[derive(Default)]
pub struct EngineHost {
    busy: std::sync::atomic::AtomicBool,
    state: Arc<HostState>,
}

#[tauri::command]
pub async fn engine_start(state: tauri::State<'_, EngineHost>, app: tauri::AppHandle) -> Result<Value, String> {
    let host = state.state.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut slot = host.session.lock().map_err(|_| "Engine host is unavailable")?;
        if let Some(existing) = slot.as_ref().filter(|session| session.connected()) { return Ok(json!({"generation":existing.generation})); }
        slot.take();
        let generation = host.generation.fetch_add(1, Ordering::Relaxed) + 1;
        let events = Arc::new(move |payload| { let _ = app.emit("machina:engine", json!({"generation":generation,"payload":payload})); });
        let session = Arc::new(session::EngineSession::start(generation, events)?);
        *slot = Some(session);
        Ok(json!({"generation":generation}))
    }).await.map_err(|_| "Engine startup worker failed".to_owned())?
}

#[tauri::command]
pub async fn engine_request(state: tauri::State<'_, EngineHost>, generation: u64, method: String, params: Value) -> Result<Value, String> {
    let session = state.state.session.lock().map_err(|_| "Engine host is unavailable")?.as_ref().cloned().ok_or("Engine is not started")?;
    if generation != session.generation { return Err("This engine connection has been replaced".into()); }
    tauri::async_runtime::spawn_blocking(move || session.request(&method, params)).await.map_err(|_| "Engine request worker failed".to_owned())?
}

#[tauri::command]
pub async fn engine_stop(state: tauri::State<'_, EngineHost>, generation: u64) -> Result<(), String> {
    let session = {
        let mut slot = state.state.session.lock().map_err(|_| "Engine host is unavailable")?;
        if slot.as_ref().is_some_and(|session| session.generation != generation) { return Err("This engine connection has been replaced".into()); }
        slot.take()
    };
    tauri::async_runtime::spawn_blocking(move || { if let Some(session) = session { session.stop(); } }).await.map_err(|_| "Engine shutdown worker failed".to_owned())
}

impl Drop for EngineHost {
    fn drop(&mut self) {
        if let Ok(mut slot) = self.state.session.lock() { if let Some(session) = slot.take() { session.stop(); } }
    }
}

#[tauri::command]
pub async fn engine_probe(state: tauri::State<'_, EngineHost>) -> Result<serde_json::Value, String> {
    use std::sync::atomic::Ordering;
    if state.busy.swap(true, Ordering::AcqRel) {
        return Err("An engine connection check is already running".into());
    }
    let result = tauri::async_runtime::spawn_blocking(probe::run).await;
    state.busy.store(false, Ordering::Release);
    result.map_err(|_| "Engine connection worker failed".to_owned())?
}
