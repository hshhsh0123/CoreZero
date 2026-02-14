#[cfg(target_os = "windows")]
use windows::Win32::System::Threading::{
    GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_TIME_CRITICAL,
};

#[cfg(target_os = "windows")]
pub fn elevate_to_realtime(core_mask: usize) -> Result<(), ()> {
    unsafe {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);

        if core_mask != 0 {
            let _ = windows::Win32::System::Threading::SetThreadAffinityMask(
                GetCurrentThread(),
                core_mask.into(),
            );
        }
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
pub fn elevate_to_realtime(_core_mask: usize) -> Result<(), ()> {
    // Linux/macOS에서는 권한 필요
    // 향후 SCHED_FIFO 구현 가능
    Ok(())
}