//! Queue Backend Abstraction
//!
//! This module provides a pluggable backend system for QueueFS, allowing different
//! storage implementations (memory, SQLite, etc.) while maintaining a consistent interface.

use crate::core::errors::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::time::SystemTime;
use uuid::Uuid;

/// A message in the queue
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    /// Unique identifier for the message
    pub id: String,
    /// Message data
    pub data: Vec<u8>,
    /// Timestamp when the message was enqueued
    pub timestamp: SystemTime,
}

impl Message {
    /// Create a new message with the given data
    pub fn new(data: Vec<u8>) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            data,
            timestamp: SystemTime::now(),
        }
    }
}

/// Queue backend trait for pluggable storage implementations
pub trait QueueBackend: Send + Sync {
    /// Create a new queue with the given name
    fn create_queue(&mut self, name: &str) -> Result<()>;

    /// Remove a queue and all its messages
    fn remove_queue(&mut self, name: &str) -> Result<()>;

    /// Check if a queue exists
    fn queue_exists(&self, name: &str) -> bool;

    /// List all queues with the given prefix
    /// If prefix is empty, returns all queues
    fn list_queues(&self, prefix: &str) -> Vec<String>;

    /// Add a message to the queue
    fn enqueue(&mut self, queue_name: &str, msg: Message) -> Result<()>;

    /// Remove and return the first message from the queue
    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>>;

    /// View the first message without removing it
    fn peek(&self, queue_name: &str) -> Result<Option<Message>>;

    /// Get the number of messages in the queue
    fn size(&self, queue_name: &str) -> Result<usize>;

    /// Clear all messages from the queue
    fn clear(&mut self, queue_name: &str) -> Result<()>;

    /// Get the last enqueue time for the queue
    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime>;

    /// Acknowledge (delete) a message by ID
    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool>;
}

/// A single queue with its messages
struct Queue {
    messages: VecDeque<Message>,
    last_enqueue_time: SystemTime,
}

impl Queue {
    fn new() -> Self {
        Self {
            messages: VecDeque::new(),
            last_enqueue_time: SystemTime::UNIX_EPOCH,
        }
    }
}

/// In-memory queue backend using HashMap
pub struct MemoryBackend {
    queues: HashMap<String, Queue>,
}

impl MemoryBackend {
    /// Create a new memory backend
    pub fn new() -> Self {
        Self {
            queues: HashMap::new(),
        }
    }
}

impl QueueBackend for MemoryBackend {
    fn create_queue(&mut self, name: &str) -> Result<()> {
        if self.queues.contains_key(name) {
            return Err(Error::AlreadyExists(format!("queue '{}' already exists", name)));
        }
        self.queues.insert(name.to_string(), Queue::new());
        Ok(())
    }

    fn remove_queue(&mut self, name: &str) -> Result<()> {
        if self.queues.remove(name).is_none() {
            return Err(Error::NotFound(format!("queue '{}' not found", name)));
        }
        Ok(())
    }

    fn queue_exists(&self, name: &str) -> bool {
        self.queues.contains_key(name)
    }

    fn list_queues(&self, prefix: &str) -> Vec<String> {
        if prefix.is_empty() {
            self.queues.keys().cloned().collect()
        } else {
            self.queues
                .keys()
                .filter(|name| name.starts_with(prefix))
                .cloned()
                .collect()
        }
    }

    fn enqueue(&mut self, queue_name: &str, msg: Message) -> Result<()> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        queue.last_enqueue_time = SystemTime::now();
        queue.messages.push_back(msg);
        Ok(())
    }

    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.messages.pop_front())
    }

    fn peek(&self, queue_name: &str) -> Result<Option<Message>> {
        let queue = self.queues.get(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.messages.front().cloned())
    }

    fn size(&self, queue_name: &str) -> Result<usize> {
        let queue = self.queues.get(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.messages.len())
    }

    fn clear(&mut self, queue_name: &str) -> Result<()> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        queue.messages.clear();
        Ok(())
    }

    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime> {
        let queue = self.queues.get(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.last_enqueue_time)
    }

    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        // Find and remove message by ID
        let original_len = queue.messages.len();
        queue.messages.retain(|msg| msg.id != msg_id);
        Ok(queue.messages.len() != original_len)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_queue() {
        let mut backend = MemoryBackend::new();

        backend.create_queue("test").unwrap();
        assert!(backend.queue_exists("test"));

        // Creating duplicate should fail
        let result = backend.create_queue("test");
        assert!(result.is_err());
    }

    #[test]
    fn test_remove_queue() {
        let mut backend = MemoryBackend::new();

        backend.create_queue("test").unwrap();
        backend.remove_queue("test").unwrap();
        assert!(!backend.queue_exists("test"));

        // Removing non-existent queue should fail
        let result = backend.remove_queue("test");
        assert!(result.is_err());
    }

    #[test]
    fn test_list_queues() {
        let mut backend = MemoryBackend::new();

        backend.create_queue("queue1").unwrap();
        backend.create_queue("queue2").unwrap();
        backend.create_queue("logs/errors").unwrap();

        let all = backend.list_queues("");
        assert_eq!(all.len(), 3);

        let logs = backend.list_queues("logs");
        assert_eq!(logs.len(), 1);
        assert_eq!(logs[0], "logs/errors");
    }

    #[test]
    fn test_enqueue_dequeue() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("test").unwrap();

        let msg1 = Message::new(b"message 1".to_vec());
        let msg2 = Message::new(b"message 2".to_vec());

        backend.enqueue("test", msg1.clone()).unwrap();
        backend.enqueue("test", msg2.clone()).unwrap();

        assert_eq!(backend.size("test").unwrap(), 2);

        let dequeued1 = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued1.data, b"message 1");

        let dequeued2 = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued2.data, b"message 2");

        assert_eq!(backend.size("test").unwrap(), 0);
        assert!(backend.dequeue("test").unwrap().is_none());
    }

    #[test]
    fn test_peek() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("test").unwrap();

        let msg = Message::new(b"test message".to_vec());
        backend.enqueue("test", msg.clone()).unwrap();

        let peeked1 = backend.peek("test").unwrap().unwrap();
        assert_eq!(peeked1.data, b"test message");

        let peeked2 = backend.peek("test").unwrap().unwrap();
        assert_eq!(peeked2.data, b"test message");

        // Size should still be 1
        assert_eq!(backend.size("test").unwrap(), 1);
    }

    #[test]
    fn test_clear() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("test").unwrap();

        backend.enqueue("test", Message::new(b"msg1".to_vec())).unwrap();
        backend.enqueue("test", Message::new(b"msg2".to_vec())).unwrap();

        assert_eq!(backend.size("test").unwrap(), 2);

        backend.clear("test").unwrap();
        assert_eq!(backend.size("test").unwrap(), 0);
    }

    #[test]
    fn test_multi_queue_isolation() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("queue1").unwrap();
        backend.create_queue("queue2").unwrap();

        backend.enqueue("queue1", Message::new(b"msg1".to_vec())).unwrap();
        backend.enqueue("queue2", Message::new(b"msg2".to_vec())).unwrap();

        assert_eq!(backend.size("queue1").unwrap(), 1);
        assert_eq!(backend.size("queue2").unwrap(), 1);

        let msg1 = backend.dequeue("queue1").unwrap().unwrap();
        assert_eq!(msg1.data, b"msg1");

        // queue2 should be unaffected
        assert_eq!(backend.size("queue2").unwrap(), 1);
    }

    #[test]
    fn test_operations_on_nonexistent_queue() {
        let mut backend = MemoryBackend::new();

        assert!(backend.enqueue("nonexistent", Message::new(b"data".to_vec())).is_err());
        assert!(backend.dequeue("nonexistent").is_err());
        assert!(backend.peek("nonexistent").is_err());
        assert!(backend.size("nonexistent").is_err());
        assert!(backend.clear("nonexistent").is_err());
    }
}
