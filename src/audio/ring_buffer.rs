use std::sync::atomic::{AtomicUsize, Ordering};
use std::cell::UnsafeCell;
use std::mem::MaybeUninit;

const CAPACITY: usize = 1 << 20;  // 1,048,576 samples (~10초 @48kHz stereo)
const MASK: usize = CAPACITY - 1;

#[repr(align(64))]
struct PaddedAtomicUsize(AtomicUsize);

pub struct RingBuffer {
    buffer: UnsafeCell<[MaybeUninit<f32>; CAPACITY]>,
    head: PaddedAtomicUsize,
    tail: PaddedAtomicUsize,
}

unsafe impl Sync for RingBuffer {}

impl RingBuffer {
    pub fn new() -> Self {
        Self {
            buffer: UnsafeCell::new([MaybeUninit::uninit(); CAPACITY]),
            head: PaddedAtomicUsize(AtomicUsize::new(0)),
            tail: PaddedAtomicUsize(AtomicUsize::new(0)),
        }
    }

    pub fn push(&self, sample: f32) -> bool {
        let head = self.head.0.load(Ordering::Relaxed);
        let tail = self.tail.0.load(Ordering::Acquire);

        if head.wrapping_sub(tail) == CAPACITY {
            return false;
        }

        unsafe {
            (*self.buffer.get())[head & MASK].write(sample);
        }

        self.head.0.store(head.wrapping_add(1), Ordering::Release);
        true
    }

    pub fn pop(&self) -> Option<f32> {
        let tail = self.tail.0.load(Ordering::Relaxed);
        let head = self.head.0.load(Ordering::Acquire);

        if head == tail {
            return None;
        }

        let sample = unsafe {
            (*self.buffer.get())[tail & MASK].assume_init_read()
        };

        self.tail.0.store(tail.wrapping_add(1), Ordering::Release);
        Some(sample)
    }

    pub fn available_read(&self) -> usize {
        let head = self.head.0.load(Ordering::Acquire);
        let tail = self.tail.0.load(Ordering::Relaxed);
        head.wrapping_sub(tail)
    }
}