//! Integration checks against the host the tests run on.
//!
//! These are read-only: no writes, no child processes, no signals.

use pa_runtime::{human, Level, Snapshot, Thresholds, MIB};

#[test]
fn probe_real_host() {
    let snapshot = Snapshot::take().expect("/proc/meminfo must be readable");
    assert!(snapshot.mem.total > 0, "total memory must be positive");
    assert!(snapshot.mem.available <= snapshot.mem.total);
    assert!(snapshot.cpus >= 1);
    assert!(!human(snapshot.mem.total).is_empty());
}

#[test]
fn verdict_is_within_bounds() {
    let snapshot = Snapshot::take().expect("/proc/meminfo must be readable");
    let thresholds = Thresholds::default();
    let decision = snapshot.decide(&thresholds, None, None);
    assert!(decision.concurrency >= 1);
    assert!(decision.concurrency <= thresholds.max_concurrency);
    assert!(decision.cpu_cap >= 1);
    assert!(matches!(decision.level, Level::Low | Level::Medium | Level::High));
    assert!(!decision.reasons.is_empty());
}

#[test]
fn override_is_honoured() {
    let snapshot = Snapshot::take().expect("/proc/meminfo must be readable");
    let thresholds = Thresholds::default();
    let forced = snapshot.decide(&thresholds, Some(Level::Low), None);
    assert_eq!(forced.level, Level::Low);
    assert_eq!(forced.concurrency, 1);
}

#[test]
fn effective_memory_never_exceeds_physical() {
    let snapshot = Snapshot::take().expect("/proc/meminfo must be readable");
    assert!(snapshot.effective_available() <= snapshot.mem.total);
    assert!(snapshot.effective_available() <= snapshot.mem.available);
    assert!(snapshot.effective_total() > MIB, "host reports under 1 MiB total");
}