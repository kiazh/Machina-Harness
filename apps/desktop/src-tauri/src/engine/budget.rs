use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};

#[derive(Clone)]
pub struct ByteBudget { used: Arc<AtomicUsize>, limit: usize }
pub struct ByteLease { used: Arc<AtomicUsize>, bytes: usize }
impl ByteBudget {
    pub fn new(limit: usize) -> Self { Self { used: Arc::new(AtomicUsize::new(0)), limit } }
    pub fn reserve(&self, bytes: usize) -> Result<ByteLease, String> {
        let mut used = self.used.load(Ordering::Acquire);
        loop {
            let next = used.checked_add(bytes).filter(|next| *next <= self.limit).ok_or("Engine channel byte budget is full")?;
            match self.used.compare_exchange_weak(used, next, Ordering::AcqRel, Ordering::Acquire) {
                Ok(_) => break,
                Err(current) => used = current,
            }
        }
        Ok(ByteLease { used: self.used.clone(), bytes })
    }
}
impl Drop for ByteLease { fn drop(&mut self) { self.used.fetch_sub(self.bytes, Ordering::AcqRel); } }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bytes_are_bounded_across_frames_and_released_on_drop() {
        let budget = ByteBudget::new(10);
        let first = budget.reserve(6).unwrap();
        assert!(budget.reserve(5).is_err());
        let second = budget.reserve(4).unwrap();
        assert!(budget.reserve(1).is_err());
        drop(first);
        assert!(budget.reserve(6).is_ok());
        drop(second);
        assert!(budget.reserve(10).is_ok());
    }
}
