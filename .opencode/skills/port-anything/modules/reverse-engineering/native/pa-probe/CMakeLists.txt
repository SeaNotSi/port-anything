# pa-probe — native container/architecture probe
#
# A dependency-free C++17 helper that identifies a target container from its
# magic bytes and reports the machine word from the header. It is the native
# counterpart to `pa.re.sniff` / `pa.re.analyze`: same offsets, same answer, no
# Python runtime. Built as a static library plus a CLI, with a ctest suite.

cmake_minimum_required(VERSION 3.16)
project(pa-probe LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "build type" FORCE)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

add_library(pa_probe STATIC src/pa_probe.cpp)
target_include_directories(pa_probe PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

add_executable(pa-probe src/probe_main.cpp)
target_link_libraries(pa-probe PRIVATE pa_probe)

enable_testing()

add_executable(pa_probe_tests tests/probe_test.cpp)
target_link_libraries(pa_probe_tests PRIVATE pa_probe)
add_test(NAME pa_probe_tests COMMAND pa_probe_tests)

# Write a small ELF fixture, then run the CLI against it end to end.
add_test(
  NAME pa_probe_fixture_setup
  COMMAND pa_probe_tests --write-fixture ${CMAKE_CURRENT_BINARY_DIR}/fixture.elf
)
set_tests_properties(pa_probe_fixture_setup PROPERTIES FIXTURES_SETUP pa_probe_fixtures)

add_test(NAME pa_probe_cli COMMAND pa-probe ${CMAKE_CURRENT_BINARY_DIR}/fixture.elf)
set_tests_properties(pa_probe_cli PROPERTIES FIXTURES_REQUIRED pa_probe_fixtures)
