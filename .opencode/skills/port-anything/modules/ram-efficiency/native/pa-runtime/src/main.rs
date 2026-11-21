//! `pa-runtime` — print the memory verdict as JSON or text.
//!
//! Exit codes: 0 verdict produced, 2 the host could not be sampled.
//! The binary never fails the build when memory is low; it reports `low`.

use std::process::ExitCode;

use pa_runtime::{human, Decision, Level, Snapshot, Thresholds};

const USAGE: &str = "\
usage: pa-runtime [--json] [--override LEVEL|N] [--help] [--version]

  --json            machine-readable verdict
  --override LEVEL  force low, medium or high
  --override N      force a concurrency count (clamped by the cpu reserve)
";

fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            ch if (ch as u32) < 0x20 => out.push(' '),
            ch => out.push(ch),
        }
    }
    out
}

fn json(decision: &Decision, snapshot: &Snapshot) -> String {
    let reasons = decision
        .reasons
        .iter()
        .map(|reason| format!("\"{}\"", escape(reason)))
        .collect::<Vec<_>>()
        .join(",");
    let load1 = snapshot
        .load1
        .map(|value| format!("{value:.2}"))
        .unwrap_or_else(|| "null".to_string());
    let cgroup_limit = snapshot
        .cgroup_limit
        .map(|value| value.to_string())
        .unwrap_or_else(|| "null".to_string());
    let cgroup_usage = snapshot
        .cgroup_usage
        .map(|value| value.to_string())
        .unwrap_or_else(|| "null".to_string());
    format!(
        concat!(
            "{{\"available\":{},\"total\":{},\"effective_available\":{},",
            "\"cgroup_limit\":{},\"cgroup_usage\":{},\"cpus\":{},\"load1\":{},",
            "\"level\":\"{}\",\"concurrency\":{},\"budget_bytes\":{},\"cpu_cap\":{},",
            "\"reasons\":[{}]}}"
        ),
        snapshot.mem.available,
        snapshot.mem.total,
        snapshot.effective_available(),
        cgroup_limit,
        cgroup_usage,
        snapshot.cpus,
        load1,
        decision.level.as_str(),
        decision.concurrency,
        decision.budget_bytes,
        decision.cpu_cap,
        reasons
    )
}

fn text(decision: &Decision, snapshot: &Snapshot) -> String {
    let available = snapshot.effective_available();
    let total = snapshot.effective_total();
    let percent = if total == 0 {
        0.0
    } else {
        available as f64 / total as f64 * 100.0
    };
    let mut lines = vec![
        format!(
            "available : {} of {} ({:.1}%)",
            human(available),
            human(total),
            percent
        ),
        format!(
            "swap      : {} free of {}",
            human(snapshot.mem.swap_free),
            human(snapshot.mem.swap_total)
        ),
        format!(
            "load      : {} on {} cpus",
            snapshot
                .load1
                .map(|value| format!("{value:.2}"))
                .unwrap_or_else(|| "unknown".to_string()),
            snapshot.cpus
        ),
    ];
    if let Some(limit) = snapshot.cgroup_limit {
        lines.push(format!("cgroup    : {} limit", human(limit)));
    }
    lines.push(format!(
        "level     : {} -> concurrency {}",
        decision.level.as_str().to_ascii_uppercase(),
        decision.concurrency
    ));
    lines.push(format!(
        "budget    : {} above the reserve",
        human(decision.budget_bytes)
    ));
    for reason in &decision.reasons {
        lines.push(format!("reason    : {reason}"));
    }
    lines.join("\n")
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut as_json = false;
    let mut override_level: Option<Level> = None;
    let mut override_concurrency: Option<u32> = None;
    let mut index = 0;

    while index < args.len() {
        match args[index].as_str() {
            "--json" => as_json = true,
            "--help" | "-h" => {
                println!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            "--version" | "-V" => {
                println!("pa-runtime {}", env!("CARGO_PKG_VERSION"));
                return ExitCode::SUCCESS;
            }
            "--override" => {
                let Some(value) = args.get(index + 1) else {
                    eprintln!("pa-runtime: --override needs a value\n{USAGE}");
                    return ExitCode::from(2);
                };
                if let Some(level) = Level::parse(value) {
                    override_level = Some(level);
                } else if let Ok(count) = value.parse::<u32>() {
                    override_concurrency = Some(count);
                } else {
                    eprintln!("pa-runtime: --override expects low, medium, high or a number");
                    return ExitCode::from(2);
                }
                index += 1;
            }
            other => {
                eprintln!("pa-runtime: unknown argument {other:?}\n{USAGE}");
                return ExitCode::from(2);
            }
        }
        index += 1;
    }

    let snapshot = match Snapshot::take() {
        Ok(snapshot) => snapshot,
        Err(error) => {
            eprintln!("pa-runtime: cannot sample this host: {error}");
            return ExitCode::from(2);
        }
    };
    let thresholds = Thresholds::default();
    let decision = snapshot.decide(&thresholds, override_level, override_concurrency);

    if as_json {
        println!("{}", json(&decision, &snapshot));
    } else {
        println!("{}", text(&decision, &snapshot));
    }
    ExitCode::SUCCESS
}