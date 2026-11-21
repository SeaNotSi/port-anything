//! Memory probe and concurrency verdict, dependency-free.
//!
//! This crate exists so a build step can ask "how much can I run right now"
//! without starting Python. It reads the same sources the Python module reads —
//! `/proc/meminfo`, `/proc/loadavg`, and the cgroup memory limit — and applies
//! the same thresholds, so the two never disagree about a device.
//!
//! Parsing is separated from reading on purpose: every parser takes a `&str`,
//! which is what the tests exercise. Nothing here samples in a loop, spawns a
//! process, or sends a signal.

use std::fs;
use std::io;
use std::thread;

pub const KIB: u64 = 1024;
pub const MIB: u64 = 1024 * KIB;
pub const GIB: u64 = 1024 * MIB;

/// A cgroup v1 limit above this value means "unlimited", not 16 EiB.
const UNLIMITED_CGROUP: u64 = 1u64 << 62;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MemInfo {
    pub total: u64,
    pub available: u64,
    pub free: u64,
    pub buffers: u64,
    pub cached: u64,
    pub swap_total: u64,
    pub swap_free: u64,
}

impl MemInfo {
    pub fn used(&self) -> u64 {
        self.total.saturating_sub(self.available)
    }

    pub fn percent_used(&self) -> f64 {
        if self.total == 0 {
            0.0
        } else {
            self.used() as f64 / self.total as f64
        }
    }
}

/// Parse `/proc/meminfo`. Values are in KiB in the file and bytes out of here.
///
/// `MemAvailable` is absent on old kernels; the fallback is free + buffers +
/// cached, which is what the kernel itself used to report.
pub fn parse_meminfo(text: &str) -> Option<MemInfo> {
    let mut total: Option<u64> = None;
    let mut available: Option<u64> = None;
    let mut free: Option<u64> = None;
    let mut buffers = 0u64;
    let mut cached = 0u64;
    let mut swap_total = 0u64;
    let mut swap_free = 0u64;

    for line in text.lines() {
        let Some((key, rest)) = line.split_once(':') else {
            continue;
        };
        let Some(value) = rest
            .split_whitespace()
            .next()
            .and_then(|raw| raw.parse::<u64>().ok())
        else {
            continue;
        };
        let bytes = value.saturating_mul(KIB);
        match key.trim() {
            "MemTotal" => total = Some(bytes),
            "MemAvailable" => available = Some(bytes),
            "MemFree" => free = Some(bytes),
            "Buffers" => buffers = bytes,
            "Cached" => cached = bytes,
            "SwapTotal" => swap_total = bytes,
            "SwapFree" => swap_free = bytes,
            _ => {}
        }
    }

    let total = total?;
    let available = available.unwrap_or_else(|| {
        free.unwrap_or(0)
            .saturating_add(buffers)
            .saturating_add(cached)
    });
    Some(MemInfo {
        total,
        available,
        free: free.unwrap_or(0),
        buffers,
        cached,
        swap_total,
        swap_free,
    })
}

pub fn read_meminfo() -> io::Result<MemInfo> {
    let text = fs::read_to_string("/proc/meminfo")?;
    parse_meminfo(&text)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "/proc/meminfo had no MemTotal"))
}

/// Parse a cgroup memory limit. `max` and absurdly large values mean unlimited.
pub fn parse_cgroup_limit(text: &str) -> Option<u64> {
    let trimmed = text.trim();
    if trimmed.is_empty() || trimmed == "max" {
        return None;
    }
    let value = trimmed.parse::<u64>().ok()?;
    if value >= UNLIMITED_CGROUP {
        return None;
    }
    Some(value)
}

/// Read the cgroup limit from v2 first, then v1. Missing means unlimited.
pub fn read_cgroup_limit() -> Option<u64> {
    for path in [
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ] {
        if let Ok(text) = fs::read_to_string(path) {
            if let Some(limit) = parse_cgroup_limit(&text) {
                return Some(limit);
            }
        }
    }
    None
}

pub fn read_cgroup_usage() -> Option<u64> {
    for path in [
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    ] {
        if let Ok(text) = fs::read_to_string(path) {
            if let Ok(value) = text.trim().parse::<u64>() {
                return Some(value);
            }
        }
    }
    None
}

pub fn cpu_count() -> usize {
    thread::available_parallelism()
        .map(|value| value.get())
        .unwrap_or(1)
}

/// First field of `/proc/loadavg`.
pub fn parse_load1(text: &str) -> Option<f64> {
    text.split_whitespace().next()?.parse::<f64>().ok()
}

pub fn read_load1() -> Option<f64> {
    fs::read_to_string("/proc/loadavg")
        .ok()
        .and_then(|text| parse_load1(&text))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Low,
    Medium,
    High,
}

impl Level {
    pub fn as_str(self) -> &'static str {
        match self {
            Level::Low => "low",
            Level::Medium => "medium",
            Level::High => "high",
        }
    }

    pub fn parse(text: &str) -> Option<Level> {
        match text.trim().to_ascii_lowercase().as_str() {
            "low" => Some(Level::Low),
            "medium" => Some(Level::Medium),
            "high" => Some(Level::High),
            _ => None,
        }
    }

    fn index(self) -> usize {
        match self {
            Level::Low => 0,
            Level::Medium => 1,
            Level::High => 2,
        }
    }

    fn downgrade(self) -> Level {
        match self {
            Level::High => Level::Medium,
            Level::Medium | Level::Low => Level::Low,
        }
    }

    pub fn concurrency(self, thresholds: &Thresholds) -> u32 {
        thresholds.concurrency[self.index()]
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Thresholds {
    pub low_available_ratio: f64,
    pub medium_available_ratio: f64,
    pub low_available_bytes: u64,
    pub min_free_bytes: u64,
    pub reserve_cpu_factor: f64,
    pub max_concurrency: u32,
    pub concurrency: [u32; 3],
}

impl Default for Thresholds {
    fn default() -> Self {
        Self {
            low_available_ratio: 0.10,
            medium_available_ratio: 0.25,
            low_available_bytes: 512 * MIB,
            min_free_bytes: 384 * MIB,
            reserve_cpu_factor: 0.5,
            max_concurrency: 8,
            concurrency: [1, 2, 3],
        }
    }
}

#[derive(Debug, Clone)]
pub struct Decision {
    pub level: Level,
    pub concurrency: u32,
    pub budget_bytes: u64,
    pub cpu_cap: u32,
    pub reasons: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct Snapshot {
    pub mem: MemInfo,
    pub cgroup_limit: Option<u64>,
    pub cgroup_usage: Option<u64>,
    pub cpus: usize,
    pub load1: Option<f64>,
}

impl Snapshot {
    pub fn take() -> io::Result<Snapshot> {
        Ok(Snapshot {
            mem: read_meminfo()?,
            cgroup_limit: read_cgroup_limit(),
            cgroup_usage: read_cgroup_usage(),
            cpus: cpu_count(),
            load1: read_load1(),
        })
    }

    /// Memory the device will actually part with: the smaller of what the
    /// kernel reports and what the cgroup allows, because a container can see
    /// the host's `MemAvailable` while being limited far below it.
    pub fn effective_available(&self) -> u64 {
        match (self.cgroup_limit, self.cgroup_usage) {
            (Some(limit), Some(used)) => self
                .mem
                .available
                .min(limit.saturating_sub(used)),
            _ => self.mem.available,
        }
    }

    pub fn effective_total(&self) -> u64 {
        match self.cgroup_limit {
            Some(limit) => self.mem.total.min(limit),
            None => self.mem.total,
        }
    }

    pub fn cpu_cap(&self, thresholds: &Thresholds) -> u32 {
        let count = self.cpus.max(1) as f64;
        let spare = (count * (1.0 - thresholds.reserve_cpu_factor)).round() as u32;
        spare
            .max(1)
            .min(thresholds.max_concurrency)
            .min(self.cpus.max(1) as u32)
    }

    pub fn classify(&self, thresholds: &Thresholds) -> (Level, Vec<String>) {
        let available = self.effective_available();
        let total = self.effective_total();
        if available == 0 || total == 0 {
            return (
                Level::Low,
                vec!["memory sample unavailable; running sequentially".to_string()],
            );
        }
        if available <= thresholds.min_free_bytes {
            return (
                Level::Low,
                vec![format!(
                    "available {} at or below reserve {}",
                    human(available),
                    human(thresholds.min_free_bytes)
                )],
            );
        }
        let ratio = available as f64 / total as f64;
        if ratio < thresholds.low_available_ratio {
            return (
                Level::Low,
                vec![format!(
                    "available {:.1}% below low band {:.0}%",
                    ratio * 100.0,
                    thresholds.low_available_ratio * 100.0
                )],
            );
        }
        if available < thresholds.low_available_bytes {
            return (
                Level::Low,
                vec![format!(
                    "available {} below low floor {}",
                    human(available),
                    human(thresholds.low_available_bytes)
                )],
            );
        }
        if ratio < thresholds.medium_available_ratio {
            return (
                Level::Medium,
                vec![format!(
                    "available {:.1}% below medium band {:.0}%",
                    ratio * 100.0,
                    thresholds.medium_available_ratio * 100.0
                )],
            );
        }
        (
            Level::High,
            vec![format!("available {:.1}% above medium band", ratio * 100.0)],
        )
    }

    pub fn decide(
        &self,
        thresholds: &Thresholds,
        override_level: Option<Level>,
        override_concurrency: Option<u32>,
    ) -> Decision {
        let (mut level, mut reasons) = self.classify(thresholds);
        let cpus = self.cpus.max(1) as f64;

        if let Some(load1) = self.load1 {
            if level != Level::Low && load1 > cpus * 0.9 {
                reasons.push(format!(
                    "load1 {:.2} above {:.2}",
                    load1,
                    cpus * 0.9
                ));
                level = level.downgrade();
            }
        }

        let cap = self.cpu_cap(thresholds);
        let mut concurrency = level.concurrency(thresholds);

        if let Some(forced) = override_level {
            level = forced;
            concurrency = forced.concurrency(thresholds);
            reasons.push(format!("user override forces {}", forced.as_str()));
        }
        if let Some(forced) = override_concurrency {
            if forced >= 1 {
                concurrency = forced;
                reasons.push(format!("user override forces concurrency {forced}"));
            } else {
                reasons.push(format!("user override {forced} ignored; concurrency is at least 1"));
            }
        }

        if concurrency > cap {
            reasons.push(format!(
                "capped at {cap} by cpu reserve and max_concurrency"
            ));
            concurrency = cap;
        }
        concurrency = concurrency.max(1);

        Decision {
            level,
            concurrency,
            budget_bytes: self
                .effective_available()
                .saturating_sub(thresholds.min_free_bytes),
            cpu_cap: cap,
            reasons,
        }
    }
}

pub fn human(bytes: u64) -> String {
    for (unit, size) in [("GiB", GIB), ("MiB", MIB), ("KiB", KIB)] {
        if bytes >= size {
            return format!("{:.1} {unit}", bytes as f64 / size as f64);
        }
    }
    format!("{bytes} B")
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "MemTotal:        7871024 kB\n\
MemFree:          512000 kB\n\
MemAvailable:    4194304 kB\n\
Buffers:           65536 kB\n\
Cached:          1048576 kB\n\
SwapTotal:       4194304 kB\n\
SwapFree:        1048576 kB\n";

    #[test]
    fn parses_kib_into_bytes() {
        let mem = parse_meminfo(SAMPLE).expect("sample should parse");
        assert_eq!(mem.total, 7871024 * 1024);
        assert_eq!(mem.available, 4194304 * 1024);
        assert_eq!(mem.free, 512000 * 1024);
        assert_eq!(mem.buffers, 65536 * 1024);
        assert_eq!(mem.cached, 1048576 * 1024);
        assert_eq!(mem.swap_total, 4194304 * 1024);
        assert_eq!(mem.swap_free, 1048576 * 1024);
    }

    #[test]
    fn falls_back_when_memavailable_missing() {
        let text = "MemTotal: 1048576 kB\nMemFree: 262144 kB\nBuffers: 131072 kB\nCached: 131072 kB\n";
        let mem = parse_meminfo(text).expect("fallback should parse");
        assert_eq!(mem.available, (262144 + 131072 + 131072) * 1024);
    }

    #[test]
    fn rejects_text_without_total() {
        assert!(parse_meminfo("MemFree: 12 kB\n").is_none());
        assert!(parse_meminfo("").is_none());
    }

    #[test]
    fn ignores_malformed_lines() {
        let text = "MemTotal: not-a-number kB\nMemTotal: 2048 kB\n";
        assert_eq!(parse_meminfo(text).unwrap().total, 2048 * 1024);
    }

    #[test]
    fn used_and_percent() {
        let mem = parse_meminfo(SAMPLE).unwrap();
        assert_eq!(mem.used(), mem.total - mem.available);
        assert!(mem.percent_used() > 0.0 && mem.percent_used() < 1.0);
    }

    #[test]
    fn cgroup_limit_parsing() {
        assert_eq!(parse_cgroup_limit("max"), None);
        assert_eq!(parse_cgroup_limit(""), None);
        assert_eq!(parse_cgroup_limit("1073741824"), Some(1073741824));
        assert_eq!(parse_cgroup_limit("9223372036854771712"), None);
        assert_eq!(parse_cgroup_limit("garbage"), None);
    }

    #[test]
    fn load_parsing() {
        assert_eq!(parse_load1("1.20 1.10 0.90 2/345 6789"), Some(1.20));
        assert_eq!(parse_load1(""), None);
        assert_eq!(parse_load1("x y z"), None);
    }

    fn snapshot_with(available: u64, total: u64, cpus: usize, load1: Option<f64>) -> Snapshot {
        Snapshot {
            mem: MemInfo {
                total,
                available,
                free: available,
                buffers: 0,
                cached: 0,
                swap_total: 0,
                swap_free: 0,
            },
            cgroup_limit: None,
            cgroup_usage: None,
            cpus,
            load1,
        }
    }

    #[test]
    fn classifies_low_by_ratio() {
        let thresholds = Thresholds::default();
        let snap = snapshot_with(8 * GIB / 20, 8 * GIB, 8, Some(0.0));
        let (level, reasons) = snap.classify(&thresholds);
        assert_eq!(level, Level::Low);
        assert!(reasons[0].contains("low band"));
    }

    #[test]
    fn absolute_floor_beats_ratio() {
        let thresholds = Thresholds::default();
        // 450 MiB of 3 GiB is 14.6%: above the low ratio, under the absolute
        // floor, and above the reserve so the floor is the rule that fires.
        let snap = snapshot_with(450 * MIB, 3 * GIB, 8, Some(0.0));
        assert!(thresholds.min_free_bytes < 450 * MIB);
        let (level, reasons) = snap.classify(&thresholds);
        assert_eq!(level, Level::Low);
        assert!(reasons[0].contains("low floor"));
    }

    #[test]
    fn classifies_medium_and_high() {
        let thresholds = Thresholds::default();
        let medium = snapshot_with(8 * GIB * 20 / 100, 8 * GIB, 8, Some(0.0));
        assert_eq!(medium.classify(&thresholds).0, Level::Medium);
        let high = snapshot_with(8 * GIB * 60 / 100, 8 * GIB, 8, Some(0.0));
        assert_eq!(high.classify(&thresholds).0, Level::High);
    }

    #[test]
    fn cgroup_limit_caps_available() {
        let thresholds = Thresholds::default();
        let mut snap = snapshot_with(6 * GIB, 8 * GIB, 8, Some(0.0));
        snap.cgroup_limit = Some(1 * GIB);
        snap.cgroup_usage = Some(900 * MIB);
        assert_eq!(snap.effective_available(), 124 * MIB);
        assert_eq!(snap.effective_total(), 1 * GIB);
        assert_eq!(snap.classify(&thresholds).0, Level::Low);
    }

    #[test]
    fn high_load_downgrades_one_step_only() {
        let thresholds = Thresholds::default();
        let snap = snapshot_with(6 * GIB, 8 * GIB, 8, Some(9.0));
        let decision = snap.decide(&thresholds, None, None);
        assert_eq!(decision.level, Level::Medium);
        assert!(decision.reasons.iter().any(|reason| reason.contains("load1")));
    }

    #[test]
    fn cpu_reserve_limits_concurrency() {
        let thresholds = Thresholds::default();
        let snap = snapshot_with(6 * GIB, 8 * GIB, 2, Some(0.0));
        let decision = snap.decide(&thresholds, None, None);
        assert_eq!(decision.cpu_cap, 1);
        assert_eq!(decision.concurrency, 1);
    }

    #[test]
    fn override_level_and_concurrency() {
        let thresholds = Thresholds::default();
        let snap = snapshot_with(6 * GIB, 8 * GIB, 8, Some(0.0));
        let forced_level = snap.decide(&thresholds, Some(Level::Low), None);
        assert_eq!(forced_level.level, Level::Low);
        assert_eq!(forced_level.concurrency, 1);
        let forced_count = snap.decide(&thresholds, None, Some(2));
        assert_eq!(forced_count.concurrency, 2);
        let clamped = snap.decide(&thresholds, None, Some(99));
        assert_eq!(clamped.concurrency, clamped.cpu_cap);
    }

    #[test]
    fn budget_saturates_at_zero() {
        let thresholds = Thresholds::default();
        let snap = snapshot_with(1024, 8 * GIB, 8, None);
        assert_eq!(snap.decide(&thresholds, None, None).budget_bytes, 0);
    }

    #[test]
    fn level_parsing() {
        assert_eq!(Level::parse("HIGH"), Some(Level::High));
        assert_eq!(Level::parse("low"), Some(Level::Low));
        assert_eq!(Level::parse(" urgent"), None);
        assert_eq!(Level::High.downgrade(), Level::Medium);
        assert_eq!(Level::Low.downgrade(), Level::Low);
    }

    #[test]
    fn human_bytes_units() {
        assert_eq!(human(512), "512 B");
        assert_eq!(human(GIB), "1.0 GiB");
        assert_eq!(human(3 * MIB / 2), "1.5 MiB");
    }
}