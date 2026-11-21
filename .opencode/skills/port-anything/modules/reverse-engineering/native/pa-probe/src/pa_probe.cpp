// pa_probe.cpp — implementation of the container/architecture probe.

#include "pa_probe.h"

#include <cstddef>

namespace pa {
namespace {

std::uint16_t read_u16(const std::vector<std::uint8_t>& data, std::size_t offset, bool little) {
    if (offset + 2 > data.size()) {
        return 0;
    }
    if (little) {
        return static_cast<std::uint16_t>(data[offset] | (data[offset + 1] << 8));
    }
    return static_cast<std::uint16_t>((data[offset] << 8) | data[offset + 1]);
}

std::uint32_t read_u32(const std::vector<std::uint8_t>& data, std::size_t offset, bool little) {
    if (offset + 4 > data.size()) {
        return 0;
    }
    if (little) {
        return static_cast<std::uint32_t>(data[offset]) |
               (static_cast<std::uint32_t>(data[offset + 1]) << 8) |
               (static_cast<std::uint32_t>(data[offset + 2]) << 16) |
               (static_cast<std::uint32_t>(data[offset + 3]) << 24);
    }
    return (static_cast<std::uint32_t>(data[offset]) << 24) |
           (static_cast<std::uint32_t>(data[offset + 1]) << 16) |
           (static_cast<std::uint32_t>(data[offset + 2]) << 8) |
           static_cast<std::uint32_t>(data[offset + 3]);
}

bool starts_with(const std::vector<std::uint8_t>& data, std::initializer_list<std::uint8_t> magic) {
    if (data.size() < magic.size()) {
        return false;
    }
    std::size_t index = 0;
    for (std::uint8_t byte : magic) {
        if (data[index++] != byte) {
            return false;
        }
    }
    return true;
}

std::string elf_machine(std::uint16_t machine) {
    switch (machine) {
        case 0x03: return "x86";
        case 0x3E: return "x86-64";
        case 0x28: return "arm";
        case 0xB7: return "aarch64";
        case 0xF3: return "riscv";
        case 0x08: return "mips";
        default: return "unknown";
    }
}

std::string pe_machine(std::uint16_t machine) {
    switch (machine) {
        case 0x014C: return "x86";
        case 0x8664: return "x86-64";
        case 0x01C0: return "arm";
        case 0x01C4: return "armv7";
        case 0xAA64: return "arm64";
        case 0x5032: return "riscv64";
        case 0x5064: return "riscv";
        default: return "unknown";
    }
}

std::string macho_machine(std::uint32_t cpu) {
    switch (cpu) {
        case 0x00000007: return "x86";
        case 0x01000007: return "x86-64";
        case 0x0000000C: return "arm";
        case 0x0100000C: return "arm64";
        default: return "unknown";
    }
}

ProbeResult probe_elf(const std::vector<std::uint8_t>& data) {
    ProbeResult result;
    result.format = Format::Elf;
    result.format_name = "elf";
    if (data.size() < 20) {
        result.note = "truncated ELF identification";
        return result;
    }
    const std::uint8_t elf_class = data[4];
    const std::uint8_t elf_data = data[5];
    const bool little = elf_data != 2;  // EI_DATA: 1 little, 2 big
    result.bits = elf_class == 2 ? "64" : (elf_class == 1 ? "32" : "");
    result.endian = little ? "little" : "big";
    result.machine = elf_machine(read_u16(data, 18, little));
    return result;
}

ProbeResult probe_pe(const std::vector<std::uint8_t>& data) {
    ProbeResult result;
    result.format = Format::Pe;
    result.format_name = "pe";
    if (data.size() < 0x40) {
        result.note = "truncated DOS header";
        return result;
    }
    const std::uint32_t pe_offset = read_u32(data, 0x3C, true);
    if (pe_offset + 6 > data.size()) {
        result.format = Format::Unknown;
        result.format_name = "unknown";
        result.note = "MZ header without a PE signature in range";
        return result;
    }
    if (data[pe_offset] != 'P' || data[pe_offset + 1] != 'E' || data[pe_offset + 2] != 0 ||
        data[pe_offset + 3] != 0) {
        result.format = Format::Unknown;
        result.format_name = "unknown";
        result.note = "MZ header without a PE signature";
        return result;
    }
    result.machine = pe_machine(read_u16(data, pe_offset + 4, true));
    result.endian = "little";
    // Optional header magic: PE32 = 0x10B, PE32+ = 0x20B. The optional header
    // starts 24 bytes past the PE signature (4 signature + 20 COFF header).
    const std::uint16_t optional_magic = read_u16(data, pe_offset + 24, true);
    if (optional_magic == 0x10B) {
        result.bits = "32";
    } else if (optional_magic == 0x20B) {
        result.bits = "64";
    }
    return result;
}

}  // namespace

std::string format_name(Format format) {
    switch (format) {
        case Format::Elf: return "elf";
        case Format::Pe: return "pe";
        case Format::MachO: return "macho";
        case Format::Zip: return "zip";
        case Format::SevenZip: return "7z";
        case Format::Rar: return "rar";
        case Format::Gzip: return "gzip";
        case Format::Bzip2: return "bzip2";
        case Format::Xz: return "xz";
        case Format::Iso9660: return "iso9660";
        case Format::GodotPck: return "godot_pck";
        case Format::UnrealPak: return "ue_pak";
        case Format::Unknown: return "unknown";
    }
    return "unknown";
}

ProbeResult probe(const std::vector<std::uint8_t>& data) {
    if (data.size() >= 4 && data[0] == 0x7F && data[1] == 'E' && data[2] == 'L' && data[3] == 'F') {
        return probe_elf(data);
    }
    if (data.size() >= 2 && data[0] == 'M' && data[1] == 'Z') {
        return probe_pe(data);
    }
    if (starts_with(data, {0xFE, 0xED, 0xFA, 0xCE}) || starts_with(data, {0xFE, 0xED, 0xFA, 0xCF}) ||
        starts_with(data, {0xCE, 0xFA, 0xED, 0xFE}) || starts_with(data, {0xCF, 0xFA, 0xED, 0xFE}) ||
        starts_with(data, {0xCA, 0xFE, 0xBA, 0xBE})) {
        ProbeResult result;
        result.format = Format::MachO;
        result.format_name = "macho";
        const bool fat = starts_with(data, {0xCA, 0xFE, 0xBA, 0xBE});
        const bool little = starts_with(data, {0xCE, 0xFA, 0xED, 0xFE}) ||
                            starts_with(data, {0xCF, 0xFA, 0xED, 0xFE});
        const bool is64 = starts_with(data, {0xFE, 0xED, 0xFA, 0xCF}) ||
                          starts_with(data, {0xCF, 0xFA, 0xED, 0xFE});
        if (fat) {
            result.note = "universal (fat) binary; per-slice architecture not resolved";
            result.bits = "32";
        } else {
            result.bits = is64 ? "64" : "32";
            result.endian = little ? "little" : "big";
            result.machine = macho_machine(read_u32(data, 4, little));
        }
        return result;
    }
    if (starts_with(data, {'P', 'K', 0x03, 0x04}) || starts_with(data, {'P', 'K', 0x05, 0x06}) ||
        starts_with(data, {'P', 'K', 0x07, 0x08})) {
        ProbeResult result;
        result.format = Format::Zip;
        result.format_name = "zip";
        result.note = "zip container (APK/AAB/jar share this magic)";
        return result;
    }
    if (starts_with(data, {'7', 'z', 0xBC, 0xAF, 0x27, 0x1C})) {
        return ProbeResult{Format::SevenZip, "7z", "", "", "", ""};
    }
    if (starts_with(data, {'R', 'a', 'r', '!', 0x1A, 0x07})) {
        return ProbeResult{Format::Rar, "rar", "", "", "", ""};
    }
    if (starts_with(data, {0x1F, 0x8B})) {
        return ProbeResult{Format::Gzip, "gzip", "", "", "", ""};
    }
    if (starts_with(data, {'B', 'Z', 'h'})) {
        return ProbeResult{Format::Bzip2, "bzip2", "", "", "", ""};
    }
    if (starts_with(data, {0xFD, '7', 'z', 'X', 'Z', 0x00})) {
        return ProbeResult{Format::Xz, "xz", "", "", "", ""};
    }
    if (data.size() >= 0x8006 && data[0x8001] == 'C' && data[0x8002] == 'D' && data[0x8003] == '0' &&
        data[0x8004] == '0' && data[0x8005] == '1') {
        return ProbeResult{Format::Iso9660, "iso9660", "", "", "", ""};
    }
    if (starts_with(data, {'G', 'D', 'P', 'C'})) {
        return ProbeResult{Format::GodotPck, "godot_pck", "", "", "", ""};
    }
    if (read_u32(data, 0, true) == 0x5A6F12E1u) {
        return ProbeResult{Format::UnrealPak, "ue_pak", "", "", "", ""};
    }
    ProbeResult result;
    result.format_name = "unknown";
    result.note = "unrecognised magic";
    return result;
}

std::string describe(const ProbeResult& result) {
    std::string line = result.format_name;
    if (!result.bits.empty()) {
        line += " " + result.bits;
    }
    if (!result.endian.empty()) {
        line += " " + result.endian;
    }
    if (!result.machine.empty()) {
        line += " machine=" + result.machine;
    }
    if (!result.note.empty()) {
        line += " (" + result.note + ")";
    }
    return line;
}

}  // namespace pa
