// pa_probe.h — container and architecture identification from header bytes.
//
// Mirrors the Python `pa.re.sniff` offsets so the native and Python probes
// agree. All reads are bounds-checked: a truncated or hostile header returns
// Unknown rather than reading past the buffer. Little-endian is assumed only
// for the fields the magic already proved to be a PE image; ELF and Mach-O
// headers carry their own endianness and are read accordingly.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace pa {

enum class Format {
    Unknown,
    Elf,
    Pe,
    MachO,
    Zip,          // also APK / AAB / .love / jar: zip-based containers
    SevenZip,
    Rar,
    Gzip,
    Bzip2,
    Xz,
    Iso9660,      // "CD001" at offset 0x01
    GodotPck,     // "GDPC"
    UnrealPak,    // 0x5A6F12E1 little-endian
};

struct ProbeResult {
    Format format = Format::Unknown;
    std::string format_name = "unknown";
    std::string machine = "";   // normalised architecture name, or ""
    std::string bits = "";      // "32" / "64" / ""
    std::string endian = "";    // "little" / "big", or ""
    std::string note = "";      // a short human-readable detail
};

// Identify the container from the first bytes of the file.
ProbeResult probe(const std::vector<std::uint8_t>& data);

// Convenience: the format name alone, or "unknown".
std::string format_name(Format format);

// A one-line report, e.g. "elf 64 little machine=x86-64".
std::string describe(const ProbeResult& result);

}  // namespace pa
