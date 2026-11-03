use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Mutex};

type Reply = Result<Value, String>;
#[derive(Default)]
struct Registry {
    closed: bool,
    replies: HashMap<u64, mpsc::SyncSender<Reply>>,
}
#[derive(Default)]
pub struct PendingRequests {
    next: AtomicU64,
    registry: Mutex<Registry>,
}

impl PendingRequests {
    pub fn reserve(&self) -> Result<(u64, mpsc::Receiver<Reply>), String> {
        let mut registry = self.registry.lock().map_err(|_| "Engine response registry failed")?;
        if registry.closed { return Err("Engine disconnected".into()); }
        if registry.replies.len() >= 64 { return Err("Too many engine requests are pending".into()); }
        let id = self.next.fetch_add(1, Ordering::Relaxed) + 1;
        let (sender, receiver) = mpsc::sync_channel(1);
        registry.replies.insert(id, sender);
        Ok((id, receiver))
    }

    pub fn remove(&self, id: u64) {
        if let Ok(mut registry) = self.registry.lock() { registry.replies.remove(&id); }
    }

    pub fn resolve(&self, frame: &Value) {
        let Some(id) = frame.get("id").and_then(Value::as_u64) else { return; };
        let sender = self.registry.lock().ok().and_then(|mut registry| registry.replies.remove(&id));
        let Some(sender) = sender else { return; };
        let result = if frame.get("jsonrpc").and_then(Value::as_str) != Some("2.0") ||
            frame.get("result").is_some() == frame.get("error").is_some() {
            Err("Malformed engine response".into())
        } else if let Some(result) = frame.get("result") { Ok(result.clone()) }
        else {
            // Backend errors are already sanitized at the gateway boundary.
            let message = frame.pointer("/error/message").and_then(Value::as_str).unwrap_or("Engine request failed");
            Err(message.chars().take(1000).collect())
        };
        let _ = sender.send(result);
    }

    pub fn fail_all(&self, message: &str) {
        if let Ok(mut registry) = self.registry.lock() {
            registry.closed = true;
            for (_, reply) in registry.replies.drain() { let _ = reply.send(Err(message.to_owned())); }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn correlates_out_of_order_replies_without_crossing_requests() {
        let pending = PendingRequests::default();
        let (a, first) = pending.reserve().unwrap();
        let (b, second) = pending.reserve().unwrap();
        pending.resolve(&json!({"jsonrpc":"2.0","id":b,"result":"second"}));
        pending.resolve(&json!({"jsonrpc":"2.0","id":a,"result":"first"}));
        assert_eq!(first.recv().unwrap().unwrap(), "first");
        assert_eq!(second.recv().unwrap().unwrap(), "second");
    }

    #[test]
    fn bounds_pending_work_and_rejects_every_waiter_on_disconnect() {
        let pending = PendingRequests::default();
        let requests: Vec<_> = (0..64).map(|_| pending.reserve().unwrap()).collect();
        assert!(pending.reserve().is_err());
        pending.fail_all("Engine disconnected");
        for (_, reply) in requests { assert_eq!(reply.recv().unwrap().unwrap_err(), "Engine disconnected"); }
        assert!(pending.reserve().is_err(), "a disconnected registry cannot admit a late request");
    }

    #[test]
    fn ignores_unknown_ids_and_rejects_ambiguous_success_and_error() {
        let pending = PendingRequests::default();
        let (id, reply) = pending.reserve().unwrap();
        pending.resolve(&json!({"jsonrpc":"2.0","id":999,"result":"wrong"}));
        assert!(reply.try_recv().is_err());
        pending.resolve(&json!({"jsonrpc":"2.0","id":id,"result":1,"error":{"message":"private"}}));
        assert_eq!(reply.recv().unwrap().unwrap_err(), "Malformed engine response");
    }
}
