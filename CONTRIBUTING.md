# Contributing to CoreZero

Thank you for your interest in contributing to CoreZero! We welcome contributions from everyone.

## Code of Conduct

- Be respectful and inclusive
- Focus on ideas, not personalities
- Help others learn and grow

## How to Contribute

### 1. Report Bugs

**Found a bug?**

1. Check existing issues first
2. Create a new issue with:
   - Clear title
   - Detailed description
   - Steps to reproduce
   - Expected vs actual behavior
   - System info:
     ```
     OS: Windows 10
     Rust: rustc 1.75.0
     CoreZero: v1.0.0
     Audio Device: [Your device]
     ```

### 2. Suggest Features

**Have an idea?**

1. Check existing discussions
2. Open a discussion or issue with:
   - Feature title
   - Why it's useful
   - How it should work
   - Examples if possible

### 3. Submit Code

**Want to code?**

#### Setup

```bash
# Fork repository (GitHub UI)
# Clone your fork
git clone https://github.com/YOUR_USERNAME/corezero.git
cd corezero

# Create feature branch
git checkout -b feature/your-feature

# Install Rust (if needed)
rustup update
```

#### Development

```bash
# Make changes
# Format code
cargo fmt

# Check for issues
cargo clippy

# Build
cargo build --release

# Test
cargo test
./target/release/corezero "test.flac" --stress 60
```

#### Submit

```bash
# Commit with clear message
git commit -m "feat: add feature description

- What changed
- Why it changed
- Impact"

# Push to your fork
git push origin feature/your-feature

# Create Pull Request on GitHub
# - Clear title
# - Description of changes
# - Reference related issues
```

#### PR Guidelines

- One feature per PR
- Clear commit messages
- Pass `cargo fmt` and `cargo clippy`
- Include tests if possible
- Update documentation

### 4. Improve Documentation

**Found a typo or unclear explanation?**

1. Edit directly on GitHub, or
2. Fork → Edit → PR

Examples:
- Fix typos in README
- Clarify BUILD.md
- Add examples to FAQ
- Improve ARCHITECTURE.md

### 5. Test on Different Systems

**Help verify compatibility**

```bash
# Test on your system
cargo build --release
./target/release/corezero "test.flac" --stress 600

# Report results in GitHub issue:
# - OS and version
# - Audio device
# - Results (underruns, max gap, etc.)
# - Any issues encountered
```

## Development Guidelines

### Code Style

**Follow Rust conventions:**

```bash
# Format before committing
cargo fmt

# Check for issues
cargo clippy

# Run tests
cargo test
```

**Example:**

```rust
// Good: clear, documented
/// Calculates audio metrics
pub fn update_metrics(&self) {
    // Implementation
}

// Avoid: unclear, undocumented
pub fn upd(&self) {
    // ???
}
```

### Commits

**Good commit messages:**

```
feat: add WASAPI exclusive support

- Implement Windows WASAPI Exclusive mode
- Reduce latency from 20ms to 5ms
- Supports up to 192kHz
- Fixes #42
```

**Avoid:**

```
update stuff
fix bug
asdf
```

### Testing

**Before submitting:**

```bash
# Compile check
cargo check

# Format check
cargo fmt --check

# Clippy (lint) check
cargo clippy

# Build release
cargo build --release

# Run stress test
./target/release/corezero "test.flac" --stress 60
```

## Project Structure

```
corezero/
├── src/
│   ├── main.rs              # CLI entry point
│   └── audio/
│       ├── mod.rs           # Module exports
│       ├── engine.rs        # Decoding
│       ├── driver.rs        # Playback
│       ├── metrics.rs       # Metrics collection
│       ├── ring_buffer.rs   # SPSC buffer
│       └── scheduler.rs     # Thread priority
├── Cargo.toml               # Dependencies
├── README.md                # Overview
├── BUILD.md                 # Build guide
├── ARCHITECTURE.md          # Technical details
├── FAQ.md                   # Q&A
├── LICENSE                  # MIT License
└── .github/
    └── workflows/
        └── ci.yml           # CI/CD pipeline
```

## Areas for Contribution

### High Priority

- [ ] Performance testing on different systems
- [ ] WASAPI Exclusive implementation (Windows)
- [ ] ALSA exclusive mode (Linux)
- [ ] Web dashboard (UI/backend)

### Medium Priority

- [ ] DSD format support
- [ ] ALAC/AAC decoding
- [ ] Upsampling filters
- [ ] EQ/DSP features

### Low Priority

- [ ] GUI improvements
- [ ] More documentation
- [ ] Translation to other languages

## Getting Help

**Questions?**

1. Check existing discussions
2. Open a discussion with `[question]` tag
3. Ask on GitHub Discussions

**Need guidance?**

- Comment on issue you want to work on
- We'll assign and provide direction

## Recognition

Contributors will be recognized in:
- GitHub contributors page
- CHANGELOG.md
- README.md (if significant contribution)

Thank you for improving CoreZero! 🎵

---

**Questions about contributing?** Open a discussion!
