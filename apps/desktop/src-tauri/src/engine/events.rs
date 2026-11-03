use super::budget::{ByteBudget, ByteLease};
use super::framing::MAX_FRAME_BYTES;
use serde_json::{json, Value};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

pub type EventSink = Arc<dyn Fn(Value) + Send + Sync>;
struct QueuedEvent { value: Value, _lease: ByteLease }
pub struct EventDispatcher { sender: mpsc::SyncSender<QueuedEvent>, budget: ByteBudget }

fn streaming(value: &Value) -> bool { value["type"] == "message.delta" }
fn append(batch: &mut Vec<Value>, value: Value) {
    if let Some(last) = batch.last_mut() {
        if streaming(last) && streaming(&value) && last["sessionId"] == value["sessionId"] && last["turnId"] == value["turnId"] {
            if let (Some(first), Some(next)) = (last["text"].as_str(), value["text"].as_str()) {
                if first.len() + next.len() <= 16_384 {
                    last["text"] = json!(format!("{first}{next}")); return;
                }
            }
        }
    }
    batch.push(value);
}

impl EventDispatcher {
    pub fn new(sink: EventSink) -> Self {
        let (sender, receiver) = mpsc::sync_channel::<QueuedEvent>(128);
        std::thread::spawn(move || {
            while let Ok(first) = receiver.recv() {
                let deadline = Instant::now() + Duration::from_millis(16);
                let mut keep_batching = streaming(&first.value);
                let mut events = Vec::new(); let mut leases = Vec::new();
                append(&mut events, first.value); leases.push(first._lease);
                while keep_batching && leases.len() < 64 {
                    let remaining = deadline.saturating_duration_since(Instant::now());
                    if remaining.is_zero() { break; }
                    match receiver.recv_timeout(remaining) {
                        Ok(next) => { keep_batching = streaming(&next.value); append(&mut events, next.value); leases.push(next._lease); }
                        Err(_) => break,
                    }
                }
                sink(json!({"type":"desktop.batch","events":events}));
            }
            sink(json!({"type":"desktop.disconnected"}));
        });
        Self { sender, budget: ByteBudget::new(MAX_FRAME_BYTES) }
    }
    pub fn push(&self, value: Value) -> Result<(), String> {
        let size = serde_json::to_vec(&value).map_err(|_| "Could not encode engine event")?.len();
        let lease = self.budget.reserve(size)?;
        self.sender.try_send(QueuedEvent { value, _lease: lease }).map_err(|_| "Engine event queue is full".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn coalesces_adjacent_deltas_without_crossing_turns_or_tool_boundaries() {
        let mut batch = Vec::new();
        for value in [
            json!({"type":"message.delta","sessionId":"s","turnId":"t","text":"a"}),
            json!({"type":"message.delta","sessionId":"s","turnId":"t","text":"😀"}),
            json!({"type":"tool.start","id":"tool"}),
            json!({"type":"message.delta","sessionId":"s","turnId":"t","text":"b"}),
            json!({"type":"message.delta","sessionId":"s","turnId":"other","text":"c"}),
        ] { append(&mut batch, value); }
        assert_eq!(batch.len(), 4);
        assert_eq!(batch[0]["text"], "a😀");
        assert_eq!(batch[1]["type"], "tool.start");
        assert_eq!(batch[2]["text"], "b");
        assert_eq!(batch[3]["turnId"], "other");
    }
}
