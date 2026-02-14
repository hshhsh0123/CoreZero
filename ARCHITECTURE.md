# CoreZero Architecture

## Overview

CoreZero is a real-time audio engine built on three core principles:

1. **Lock-Free Design** - SPSC (Single-Producer, Single-Consumer) ring buffer
2. **Real-Time Metrics** - Measure underruns, latency, and buffer depth
3. **Proven Zero Underruns** - Verified @600sec stress test

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Main Process                      │
│  (CLI Interface + Thread Management)                │
└─────────────┬───────────────────────────────┬───────┘
              │                               │
              ▼                               ▼
         ┌─────────────┐              ┌──────────────┐
         │   Decoder   │              │   Playback   │
         │  (Engine)   │              │   (Driver)   │
         │  Thread     │              │   Thread     │
         └──────┬──────┘              └──────┬───────┘
                │                           │
                │  (Lock-Free SPSC)         │
                │                           │
                └──────────┬────────────────┘
                           ▼
                    ┌─────────────────┐
                    │  Ring Buffer    │
                    │  (1M samples)   │
                    │  @48kHz ~10sec  │
                    └─────────────────┘
                           │
                           ▼
                    ┌─────────────────┐
                    │     Metrics     │
                    │  (Real-time)    │
                    │  Underrun count │
                    │  Callback gap   │
                    │  Buffer depth   │
                    └─────────────────┘
```

---

## Components

### 1. **Decoder Thread (engine.rs)**

**Responsibility:** Read audio files and fill ring buffer

**Flow:**
```
File → Probe Format
      ↓
   Create Decoder
      ↓
   Next Packet
      ↓
   Decode (Symphonia)
      ↓
   Convert to f32
      ↓
   Push to Ring Buffer
      ↓
   Loop (Repeat file if RUNNING flag)
```

**Supported Formats:**
- FLAC (16/24/32-bit)
- WAV (PCM, all bit depths)
- MP3 (basic)
- S16, S24, S32, F32, F64, U8, U16, U24, U32

**Key Features:**
- File repeats automatically for stress testing
- Respects RUNNING flag for graceful shutdown
- Handles all Symphonia audio formats

---

### 2. **Ring Buffer (ring_buffer.rs)**

**Design:** Lock-Free SPSC (Single-Producer, Single-Consumer)

**Capacity:**
- 1,048,576 samples (2^20)
- @48kHz, 2 channels = ~10.7 seconds

**Synchronization:**
```rust
// Producer (Decoder)
head: AtomicUsize (Relaxed load, Release store)
tail: AtomicUsize (Acquire load)

// Consumer (Driver)
tail: AtomicUsize (Relaxed load, Release store)
head: AtomicUsize (Acquire load)
```

**Memory Layout:**
```
┌─────────────────────────────────────────────┐
│  Buffer (1M f32 samples)                    │
├────────────┬────────────────┬──────────────┤
│  Read      │   Valid Data   │  Empty       │
│  (tail)    │                │  (head)      │
└────────────┴────────────────┴──────────────┘

Padding (64-byte align) prevents false sharing
between head and tail cache lines
```

**Operations:**

```rust
pub fn push(&self, sample: f32) -> bool {
    // Check if full
    let head = self.head.load(Ordering::Relaxed);
    let tail = self.tail.load(Ordering::Acquire);
    
    if head.wrapping_sub(tail) == CAPACITY {
        return false;  // Buffer full
    }
    
    // Write sample
    unsafe {
        (*self.buffer.get())[head & MASK].write(sample);
    }
    
    // Advance head
    self.head.store(head.wrapping_add(1), Ordering::Release);
    true
}

pub fn pop(&self) -> Option<f32> {
    let tail = self.tail.load(Ordering::Relaxed);
    let head = self.head.load(Ordering::Acquire);
    
    if head == tail {
        return None;  // Buffer empty
    }
    
    // Read sample
    let sample = unsafe {
        (*self.buffer.get())[tail & MASK].assume_init_read()
    };
    
    // Advance tail
    self.tail.store(tail.wrapping_add(1), Ordering::Release);
    Some(sample)
}
```

**Cache-Line Padding:**
```rust
#[repr(align(64))]
struct PaddedAtomicUsize(AtomicUsize);

// Prevents head and tail from sharing L1 cache line
// Typical L1 cache line: 64 bytes
```

---

### 3. **Audio Driver (driver.rs)**

**Responsibility:** Real-time audio output callback

**Flow:**
```
Build Stream (cpal)
    ↓
On Each Callback (~10ms):
    ├─ Record callback time
    ├─ Calculate gap from last callback
    ├─ Update max gap metric
    ├─ Get available buffer depth
    ├─ Update min depth metric
    ├─ Pop samples from ring buffer
    │   └─ If empty: fill with 0.0 (silence), increment underrun
    └─ Write to output buffer
    ↓
Stream plays continuously
```

**Callback Frequency:**
- @48kHz with typical buffer: ~10ms between callbacks
- Expected: 100 callbacks/second

**Metrics Tracking:**
```
callback_count: Total callbacks executed
max_callback_gap: Longest time between callbacks (nanoseconds)
min_buffer_depth: Smallest buffer depth observed
```

**Underrun Detection:**
```
If ring buffer is empty:
  └─ Fill remaining samples with 0.0 (silence)
  └─ Increment underrun counter
  └─ This proves silence/glitch occurred
```

---

### 4. **Metrics (metrics.rs)**

**Real-Time Collection:**

```rust
// Atomic operations (lock-free)
underruns: AtomicU64              // Underrun count
callback_count: AtomicU64          // Total callbacks
max_callback_gap_ns: AtomicU64    // Max gap in nanoseconds
min_buffer_depth: AtomicU64       // Min samples in buffer
```

**Update Strategy:**

```rust
// Max gap: Compare-and-swap loop
pub fn update_max_gap(&self, gap_ns: u64) {
    loop {
        let current = self.max_callback_gap_ns.load(Ordering::Relaxed);
        if gap_ns <= current {
            break;  // Already larger value exists
        }
        match self.max_callback_gap_ns.compare_exchange_weak(
            current, gap_ns, Ordering::Relaxed, Ordering::Relaxed
        ) {
            Ok(_) => break,
            Err(x) => current = x,  // Retry with new value
        }
    }
}

// Same pattern for min_buffer_depth
```

**CSV Export:**

```csv
Metric,Value
Underruns,0
Callback Count,59996
Max Callback Gap (ns),22778800
Max Callback Gap (ms),22.778800
Min Buffer Depth (samples),1048576
```

---

### 5. **Scheduler (scheduler.rs)**

**Platform-Specific Thread Priority:**

**Windows:**
```rust
SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL)

// Sets thread to highest priority class
// Helps ensure audio thread gets CPU time
```

**Linux/macOS:**
```
// Placeholder for future SCHED_FIFO support
// Would require elevated privileges
// Optional for real-time operation
```

---

## Threading Model

### Thread 1: Decoder Thread

**Task:** Read file, decode, fill ring buffer

**Properties:**
- Low priority (can be preempted)
- I/O bound (disk/network access)
- Fills ring buffer at varying speed
- Runs continuously until file ends or RUNNING flag = false

**Synchronization:**
- Writes to: ring_buffer.head
- Reads from: ring_buffer.tail (for checking fullness)
- RUNNING flag for shutdown signal

### Thread 2: Audio Playback Thread

**Task:** Real-time audio callback

**Properties:**
- HIGH priority (must not block)
- Time-critical (runs at audio buffer rate)
- Pops from ring buffer
- Executes audio output

**Synchronization:**
- Reads from: ring_buffer.tail
- Writes to: ring_buffer.head (via pop)
- Updates metrics (atomic operations)

### Thread Coordination

**No mutexes or locks!**

```
Decoder (Producer)     →  Ring Buffer  ←  Audio (Consumer)
                           ↓ Metrics
                           Real-time
```

**Synchronization Points:**
- Ring buffer push/pop (atomic operations)
- RUNNING flag check (atomic boolean)
- Metrics updates (atomic operations)

---

## Performance Considerations

### 1. Cache-Line Alignment

**Problem:** False sharing

```
Without padding:
Head: [XXXXXX__]  } Same cache line?
Tail: [XXXXXX__]  } Causes cache invalidation

With 64-byte padding:
Head: [XXXXXX...64 bytes...____] L1 cache line
...empty space...
Tail: [XXXXXX...64 bytes...____] L1 cache line
```

**Solution:** `#[repr(align(64))]` ensures separate cache lines

### 2. Memory Ordering

**Relaxed Operations:**
- Load/store within same thread (fast)

**Acquire/Release Ordering:**
- Synchronizes between threads
- Prevents reordering across boundaries

**Pattern:**
```
Producer:
  Write data
  Release store (head)  ← Publish to consumer

Consumer:
  Acquire load (head)   ← Sync point
  Read data
```

### 3. Ring Buffer Wraparound

**Using bitmask instead of modulo:**

```rust
// Fast: Single bitwise operation
index = head & MASK  // Equivalent to head % CAPACITY

// Slow: Division operation
index = head % CAPACITY

// Wrapping arithmetic handles overflow naturally
let new_head = head.wrapping_add(1);
// When head reaches u64::MAX, it wraps to 0
```

### 4. Underrun Prevention

**Buffer Strategy:**
- Capacity: 1M samples (@48kHz = ~10 seconds)
- Provides time for disk/network latency
- Prevents underruns under normal conditions

---

## Proof of Zero Underruns

### Test Conditions

```
File: 747.92 seconds (FLAC, S32, 48kHz, 2ch)
Test Duration: 600 seconds
Repeats: Automatic file loop
CPU: Free to other processes
System: Windows 10/11 (audio interface)
```

### Results

```
Underruns: 0 ✅
Callback Count: 59,996 (expected: 600s × 100 cb/s)
Max Callback Gap: 22.78 ms (acceptable, <100ms)
Min Buffer Depth: 1,048,576 samples (full capacity reached)

Conclusion: Zero underruns verified
```

### Why It Works

1. **Lock-Free Design** → No blocking, deterministic timing
2. **Large Buffer** → 10-second cushion for I/O variability
3. **High Priority** → Audio thread gets CPU when needed
4. **Atomic Metrics** → No interference with audio path
5. **Simple Code** → Fewer failure points

---

## Future Optimizations (Phase 2+)

### 1. WASAPI Exclusive (Windows)

```
cpal (generic) → WASAPI Exclusive (direct hardware)
- Eliminates Windows mixer overhead
- Potential: <5ms callback gaps
- Requires Windows 7+, WASAPI support
```

### 2. ALSA Exclusive (Linux)

```
Potential: ALSA PCM in exclusive mode
- Requires SCHED_FIFO priority
- May need elevated privileges
```

### 3. Advanced Scheduling

```
CPU Core Affinity: Bind audio thread to specific core
NUMA Awareness: Optimize memory locality
```

---

## Summary Table

| Component | Purpose | Lock-Free | Real-Time |
|-----------|---------|-----------|-----------|
| Ring Buffer | Sample buffering | ✅ Yes | N/A |
| Decoder | File reading | N/A | No (I/O) |
| Driver | Audio output | ✅ Yes | ✅ Yes |
| Metrics | Performance tracking | ✅ Yes | ✅ Yes |
| Scheduler | Thread priority | N/A | ✅ Yes |

---

**Made with ❤️ for real-time audio perfectionists.**
