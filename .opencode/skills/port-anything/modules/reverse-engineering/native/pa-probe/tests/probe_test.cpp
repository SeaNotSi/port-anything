// probe_test.cpp — unit tests for pa::probe.
//
// Runs as its own executable so ctest can register it. `--write-fixture <path>`
// writes a small ELF file for the CLI end-to-end test.

#include "pa_probe.h"

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

namespace {

int failures = 0;
int checks = 0;

void check(bool condition, const std::string& label) {
    ++checks;
    if (!condition) {
        ++failures;
        std::fprintf(stderr, "FAIL: %s\n", label.c_str());
    }
}

std::vector<std::uint8_t> elf64(std::uint8_t machine_low, std::uint8_t machine_high, bool little) {
    std::vector<std::uint8_t> data(0x40, 0);
    data[0] = 0x7F;
    data[1] = 'E';
    data[2] = 'L';
    data[3] = 'F';
    data[4] = 2;              // ELFCLASS64
    data[5] = little ? 1 : 2; // ELFDATA2LSB / ELFDATA2MSB
    data[6] = 1;
    if (little) {
        data[18] = machine_low;
        data[19] = machine_high;
    } else {
        data[18] = machine_high;
        data[19] = machine_low;
    }
    return data;
}

std::vector<std::uint8_t> pe(std::uint16_t machine, std::uint16_t optional_magic) {
    std::vector<std::uint8_t> data(0x80, 0);
    data[0] = 'M';
    data[1] = 'Z';
    const std::uint32_t pe_offset = 0x40;
    data[0x3C] = static_cast<std::uint8_t>(pe_offset);
    data[pe_offset] = 'P';
    data[pe_offset + 1] = 'E';
    data[pe_offset + 2] = 0;
    data[pe_offset + 3] = 0;
    data[pe_offset + 4] = static_cast<std::uint8_t>(machine & 0xFF);
    data[pe_offset + 5] = static_cast<std::uint8_t>(machine >> 8);
    data[pe_offset + 24] = static_cast<std::uint8_t>(optional_magic & 0xFF);
    data[pe_offset + 25] = static_cast<std::uint8_t>(optional_magic >> 8);
    return data;
}

std::vector<std::uint8_t> macho64_le(std::uint32_t cpu) {
    std::vector<std::uint8_t> data(0x40, 0);
    data[0] = 0xCF;
    data[1] = 0xFA;
    data[2] = 0xED;
    data[3] = 0xFE;
    data[4] = static_cast<std::uint8_t>(cpu);
    data[5] = static_cast<std::uint8_t>(cpu >> 8);
    data[6] = static_cast<std::uint8_t>(cpu >> 16);
    data[7] = static_cast<std::uint8_t>(cpu >> 24);
    return data;
}

void run_checks() {
    // ELF
    auto aarch64 = elf64(0xB7, 0x00, true);
    pa::ProbeResult r = pa::probe(aarch64);
    check(r.format == pa::Format::Elf, "elf format");
    check(r.bits == "64", "elf 64 bits");
    check(r.endian == "little", "elf little endian");
    check(r.machine == "aarch64", "elf aarch64 machine");

    auto big_x86 = elf64(0x03, 0x00, false);
    r = pa::probe(big_x86);
    check(r.endian == "big", "elf big endian");
    check(r.machine == "x86", "elf x86 machine");

    // ELF truncated: still identified, but no machine read.
    std::vector<std::uint8_t> tiny_elf = {0x7F, 'E', 'L', 'F', 1, 1, 1};
    r = pa::probe(tiny_elf);
    check(r.format == pa::Format::Elf, "truncated elf still identified");
    check(r.machine.empty(), "truncated elf has no machine");
    check(!r.note.empty(), "truncated elf has a note");

    // PE
    auto pe32plus = pe(0x8664, 0x20B);
    r = pa::probe(pe32plus);
    check(r.format == pa::Format::Pe, "pe format");
    check(r.bits == "64", "pe32+ is 64 bits");
    check(r.machine == "x86-64", "pe machine x86-64");

    auto pe32 = pe(0x014C, 0x10B);
    r = pa::probe(pe32);
    check(r.bits == "32", "pe32 is 32 bits");
    check(r.machine == "x86", "pe32 machine x86");

    // MZ with no PE signature is not a PE.
    std::vector<std::uint8_t> mz_only(0x80, 0);
    mz_only[0] = 'M';
    mz_only[1] = 'Z';
    r = pa::probe(mz_only);
    check(r.format == pa::Format::Unknown, "MZ without PE signature is unknown");

    // Mach-O
    auto macho = macho64_le(0x0100000C);
    r = pa::probe(macho);
    check(r.format == pa::Format::MachO, "macho format");
    check(r.bits == "64", "macho 64 bits");
    check(r.machine == "arm64", "macho arm64 machine");

    // Zip / APK
    r = pa::probe({'P', 'K', 0x03, 0x04, 1, 2, 3});
    check(r.format == pa::Format::Zip, "zip magic");
    check(r.format_name == "zip", "zip name");

    // ISO9660
    std::vector<std::uint8_t> iso(0x8010, 0);
    iso[0x8001] = 'C';
    iso[0x8002] = 'D';
    iso[0x8003] = '0';
    iso[0x8004] = '0';
    iso[0x8005] = '1';
    check(pa::probe(iso).format == pa::Format::Iso9660, "iso9660");

    // Godot pck
    check(pa::probe({'G', 'D', 'P', 'C', 0, 0}).format == pa::Format::GodotPck, "godot pck");

    // Unreal pak: 0x5A6F12E1 little-endian.
    check(pa::probe({0xE1, 0x12, 0x6F, 0x5A}).format == pa::Format::UnrealPak, "ue pak");

    // Empty / garbage
    check(pa::probe({}).format == pa::Format::Unknown, "empty is unknown");
    check(pa::probe({1, 2, 3, 4, 5}).format == pa::Format::Unknown, "garbage is unknown");

    // describe
    check(pa::describe(pa::probe(aarch64)) == "elf 64 little machine=aarch64", "describe elf");
    check(pa::describe(pa::probe({})).find("unknown") == 0, "describe unknown");
}

int write_fixture(const char* path) {
    auto data = elf64(0xB7, 0x00, true);
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        return 1;
    }
    out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
    return out ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 3 && std::string(argv[1]) == "--write-fixture") {
        return write_fixture(argv[2]);
    }
    run_checks();
    std::printf("%d checks, %d failure(s)\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
