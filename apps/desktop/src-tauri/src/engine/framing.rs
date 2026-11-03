use serde_json::Value;
use std::io::{BufRead, Read};

pub const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;

pub struct FrameReader<R> {
    reader: R,
}

impl<R: BufRead> FrameReader<R> {
    pub fn new(reader: R) -> Self {
        Self { reader }
    }

    pub fn next_frame(&mut self) -> Result<Option<Value>, &'static str> {
        let mut frame = Vec::new();
        let read = (&mut self.reader)
            .take((MAX_FRAME_BYTES + 1) as u64)
            .read_until(b'\n', &mut frame)
            .map_err(|_| "engine channel read failed")?;
        if read == 0 {
            return Ok(None);
        }
        if read > MAX_FRAME_BYTES {
            return Err("engine frame exceeds limit");
        }
        serde_json::from_slice(&frame)
            .map(Some)
            .map_err(|_| "invalid engine JSON frame")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn reads_multiple_utf8_frames_without_splitting_unicode() {
        let bytes = "{\"result\":\"你好 🐎\"}\n{\"result\":2}\n".as_bytes();
        let mut reader = FrameReader::new(Cursor::new(bytes));
        assert_eq!(reader.next_frame().unwrap().unwrap()["result"], "你好 🐎");
        assert_eq!(reader.next_frame().unwrap().unwrap()["result"], 2);
        assert!(reader.next_frame().unwrap().is_none());
    }

    #[test]
    fn rejects_oversized_frame_before_unbounded_allocation() {
        let bytes = vec![b'x'; MAX_FRAME_BYTES + 100];
        let error = FrameReader::new(Cursor::new(bytes)).next_frame().unwrap_err();
        assert_eq!(error, "engine frame exceeds limit");
    }

    #[test]
    fn malformed_frames_do_not_echo_private_content() {
        let mut reader = FrameReader::new(Cursor::new(b"private-secret-not-json\n"));
        assert_eq!(reader.next_frame().unwrap_err(), "invalid engine JSON frame");
    }
}
