# Security Policy

## Reporting Security Issues

**Please do not open public issues for security vulnerabilities.**

If you discover a security vulnerability, please email: [your-email@example.com]

Include:
- Description of vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will respond within 48 hours.

---

## Security Considerations

### Memory Safety

CoreZero is built in Rust, which provides:

✅ **Guaranteed memory safety**
- No buffer overflows
- No use-after-free
- No data races (enforced at compile time)

✅ **Unsafe code minimized**
- Only used in ring buffer (carefully audited)
- Necessary for lock-free performance
- All unsafe blocks documented

### Unsafe Code Areas

#### 1. Ring Buffer (ring_buffer.rs)

```rust
unsafe {
    (*self.buffer.get())[index & MASK].write(sample);
}
```

**Why unsafe:** Direct memory access for performance

**Safety guarantees:**
- Index always within bounds (masked to CAPACITY-1)
- No aliasing issues (single writer/reader)
- Properly aligned memory

#### 2. Symphonia Integration (engine.rs)

Uses `assume_init_read()` for audio format conversion:

```rust
let sample = unsafe {
    (*self.buffer.get())[tail & MASK].assume_init_read()
};
```

**Why unsafe:** Audio codec output already initialized by Symphonia

**Safety guarantees:**
- Called only after MaybeUninit is written
- All paths guaranteed to write before read

### Input Validation

- ✅ File paths validated before opening
- ✅ Audio format checked before decoding
- ✅ Sample values range-checked during conversion
- ✅ CLI arguments validated
- ✅ CSV export path validated

### No Network Features

**Currently:** No network code in v1.0
- No remote connections
- No data transmission
- No internet required

**Future (v2.0):** Network features planned
- NAS support (SMB/NFS) - will be carefully audited
- Optional cloud features - opt-in only

### File Access

**Safe file handling:**
- Read-only by default (playback)
- File path explicitly specified
- No directory traversal possible
- CSV export to specified path only

### Audio Device Access

**Hardware safety:**
- Using cpal (well-tested library)
- Standard OS audio APIs
- No direct hardware access
- Device enumeration only

---

## Dependencies

### Core Dependencies

| Crate | Version | Purpose | Security |
|-------|---------|---------|----------|
| symphonia | 0.5.5 | Audio decoding | ✅ Well-maintained |
| cpal | 0.15.3 | Audio output | ✅ Industry standard |
| windows | 0.58.0 | Windows API | ✅ Official Microsoft |

### Security Updates

CoreZero will:
- ✅ Monitor dependencies for vulnerabilities
- ✅ Update promptly when issues found
- ✅ Test thoroughly before releasing
- ✅ Document breaking changes

### Vulnerability Reports

If you find a security issue in a dependency:
1. Report to that project
2. Notify CoreZero maintainers
3. We'll update and release patch

---

## Threat Model

### What We Protect Against

✅ **Memory corruption** - Rust's guarantees
✅ **Buffer overflows** - Impossible in safe code
✅ **Use-after-free** - Enforced by borrow checker
✅ **Data races** - Compile-time prevention
✅ **Malformed audio files** - Symphonia validation
✅ **Invalid paths** - Path validation

### What We Don't Protect Against

❌ **Malicious audio drivers** - OS level issue
❌ **Compromised file system** - OS responsibility
❌ **Privilege escalation** - Not applicable (user program)
❌ **DRM/Copyright protection** - Not in scope

---

## Security Best Practices for Users

### When Using CoreZero

1. **Keep Rust updated**
   ```bash
   rustup update
   ```

2. **Keep dependencies current**
   ```bash
   cargo update
   ```

3. **Use trusted audio files**
   - Download from legitimate sources
   - Verify file integrity if possible

4. **Avoid untrusted audio drivers**
   - Update audio drivers from official sources
   - Be cautious with beta drivers

5. **Check file permissions**
   - Only run on files you trust
   - Be careful with network file shares

### When Building from Source

1. **Verify repository authenticity**
   ```bash
   git verify-commit HEAD
   ```

2. **Check build environment**
   - Clean OS/tools if concerned
   - Build in isolated environment if paranoid

3. **Inspect changes**
   - Review git commits before building
   - Check file modifications

---

## Audit Status

### v1.0 Security Audit

**Self-audit completed:** ✅

- Code review: Complete
- Unsafe code audit: Complete
- Dependency check: Complete
- Threat analysis: Complete

**Third-party audit:** Not yet (open source, community review welcome)

### Areas Reviewed

- ✅ Ring buffer implementation
- ✅ Memory safety in unsafe blocks
- ✅ Input validation
- ✅ File handling
- ✅ Dependencies

---

## Future Security

### v1.1+

- [ ] Formal security audit (if funded)
- [ ] Fuzzing for audio format handling
- [ ] Comprehensive testing suite
- [ ] Security documentation expansion

### v2.0

- [ ] Network code review (SMB/NFS)
- [ ] Cloud feature security analysis
- [ ] Multi-user isolation (if applicable)

---

## Security Contacts

- **Security Issues:** [security@example.com]
- **General Questions:** [support@example.com]
- **Feature Requests:** GitHub Issues

---

## Cryptography

**CoreZero does NOT:**
- Use encryption
- Handle sensitive data
- Perform cryptographic operations
- Require cryptographic libraries

**Future consideration:** If cloud features added, will use industry-standard TLS.

---

## Compliance

### Licenses

- **MIT License:** Permissive, no restrictions on use
- **FLAC:** Open format, no licensing required
- **Rust:** Open source ecosystem

### Standards Compliance

- **FLAC Format:** RFC 5653 compliant
- **WAV Format:** RIFF standard compliance
- **Audio APIs:** OS-standard compliance

---

## Responsible Disclosure

### Timeline

1. **Security report received** → Acknowledged within 24 hours
2. **Triage** → Severity assessed within 48 hours
3. **Fix development** → Target 1 week for critical
4. **Testing** → Thorough verification
5. **Release** → Coordinated disclosure
6. **Notification** → Users informed of update

### Embargo Period

- **Critical:** Until patch released or 90 days
- **High:** Until patch released or 60 days
- **Medium:** Until patch released or 30 days
- **Low:** At maintainer discretion

---

## Security Statement

CoreZero is designed with security in mind:

**Memory Safety:** ✅ Guaranteed by Rust
**Code Quality:** ✅ Focus on simplicity and correctness
**Dependency Quality:** ✅ Carefully selected, maintained projects
**Open Source:** ✅ Community review and transparency
**User Control:** ✅ Local files only, no forced updates

---

## Questions?

Email: [your-email@example.com]

Thank you for helping keep CoreZero secure! 🔒

---

Last updated: 2025-02
Policy version: 1.0
