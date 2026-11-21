// pa-probe — identify a container from its header bytes.
//
// Usage: pa-probe <file>
// Exit codes: 0 identified, 1 I/O error, 2 usage, 3 unrecognised.

#include "pa_probe.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

const char* usage =
    "usage: pa-probe <file>\n"
    "  identify a container from its magic bytes and header fields\n";

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2 || std::string(argv[1]) == "-h" || std::string(argv[1]) == "--help") {
        std::fputs(usage, argc == 2 ? stdout : stderr);
        return 2;
    }

    std::ifstream input(argv[1], std::ios::binary);
    if (!input) {
        std::fprintf(stderr, "pa-probe: cannot open %s\n", argv[1]);
        return 1;
    }
    std::vector<std::uint8_t> data((std::istreambuf_iterator<char>(input)),
                                   std::istreambuf_iterator<char>());
    if (input.bad()) {
        std::fprintf(stderr, "pa-probe: read error on %s\n", argv[1]);
        return 1;
    }

    const pa::ProbeResult result = pa::probe(data);
    std::printf("%s: %s\n", argv[1], pa::describe(result).c_str());
    return result.format == pa::Format::Unknown ? 3 : 0;
}
