cmake_minimum_required(VERSION 3.12)

project(openviking_cpp)

include(CheckCXXCompilerFlag)

set(OV_X86_SIMD_LEVEL "AVX2" CACHE STRING "x86 SIMD level: SSE3|AVX2|AVX512|NATIVE")
set_property(CACHE OV_X86_SIMD_LEVEL PROPERTY STRINGS SSE3 AVX2 AVX512 NATIVE)

set(OV_PLATFORM_X86 OFF)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64|AMD64|i[3-6]86")
    set(OV_PLATFORM_X86 ON)
endif()

if(OV_PLATFORM_X86)
    string(TOUPPER "${OV_X86_SIMD_LEVEL}" OV_X86_SIMD_LEVEL_UPPER)
    set(OV_X86_COMPILE_FLAGS)

    if(OV_X86_SIMD_LEVEL_UPPER STREQUAL "NATIVE")
        check_cxx_compiler_flag("-march=native" HAVE_MARCH_NATIVE)
        if(HAVE_MARCH_NATIVE)
            list(APPEND OV_X86_COMPILE_FLAGS -march=native)
        else()
            message(WARNING "-march=native is not supported by this compiler; falling back to AVX2")
            set(OV_X86_SIMD_LEVEL_UPPER "AVX2")
        endif()
    endif()

    if(OV_X86_SIMD_LEVEL_UPPER STREQUAL "AVX512")
        foreach(FLAG -mavx512f -mavx512bw -mavx512dq -mavx512vl)
            string(REPLACE "-" "_" FLAG_VAR_SUFFIX "${FLAG}")
            set(FLAG_VAR "HAVE_${FLAG_VAR_SUFFIX}")
            check_cxx_compiler_flag("${FLAG}" ${FLAG_VAR})
            if(NOT ${FLAG_VAR})
                message(FATAL_ERROR "Compiler does not support ${FLAG}; cannot build with OV_X86_SIMD_LEVEL=AVX512")
            endif()
            list(APPEND OV_X86_COMPILE_FLAGS ${FLAG})
        endforeach()
    elseif(OV_X86_SIMD_LEVEL_UPPER STREQUAL "AVX2")
        check_cxx_compiler_flag("-mavx2" HAVE_MAVX2)
        if(HAVE_MAVX2)
            list(APPEND OV_X86_COMPILE_FLAGS -mavx2)
            add_compile_definitions(OV_DISABLE_AVX512=1)
            foreach(FLAG -mno-avx512f -mno-avx512bw -mno-avx512dq -mno-avx512vl)
                string(REPLACE "-" "_" FLAG_VAR_SUFFIX "${FLAG}")
                set(FLAG_VAR "HAVE_${FLAG_VAR_SUFFIX}")
                check_cxx_compiler_flag("${FLAG}" ${FLAG_VAR})
                if(${FLAG_VAR})
                    list(APPEND OV_X86_COMPILE_FLAGS ${FLAG})
                endif()
            endforeach()
        else()
            message(WARNING "Compiler does not support -mavx2; falling back to SSE3")
            set(OV_X86_SIMD_LEVEL_UPPER "SSE3")
        endif()
    endif()

    if(OV_X86_SIMD_LEVEL_UPPER STREQUAL "SSE3")
        check_cxx_compiler_flag("-msse3" HAVE_SSE3)
        if(HAVE_SSE3)
            list(APPEND OV_X86_COMPILE_FLAGS -msse3)
            add_compile_definitions(OV_DISABLE_AVX512=1)
        else()
            message(FATAL_ERROR "Compiler does not support -msse3 on x86 target")
        endif()
    endif()

    if(OV_X86_COMPILE_FLAGS)
        add_compile_options(${OV_X86_COMPILE_FLAGS})
    endif()

    message(STATUS "OpenViking x86 SIMD level: ${OV_X86_SIMD_LEVEL_UPPER}")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(APPLE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "10.15" CACHE STRING "Minimum macOS deployment version")
endif()

set(THREADS_PREFER_PTHREAD_FLAG ON)

set(CMAKE_STRIP FALSE)

add_compile_definitions(HAVE_CXX17_HAS_INCLUDE=1)
if(WIN32)
    add_compile_definitions(NOMINMAX)
endif()

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wno-error -Wno-deprecated-declarations -Wno-format -Wno-inconsistent-missing-override")

set(CMAKE_CXX_LINK_EXECUTABLE "${CMAKE_CXX_LINK_EXECUTABLE} -lpthread")

set(Python3_ARCH_INCLUDE_DIR "/usr/include/${CMAKE_SYSTEM_PROCESSOR}-linux-gnu/")

find_package(Python3 COMPONENTS Interpreter Development REQUIRED)

# On Linux, pybind11 modules don't need to link against libpython
# This prevents issues with static libpython that wasn't built with -fPIC
if(UNIX AND NOT APPLE)
    set(Python3_LIBRARIES "")
endif()

find_package(pybind11 REQUIRED)
find_package(Threads REQUIRED)

# Force static libraries for dependencies
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(LEVELDB_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LEVELDB_BUILD_BENCHMARKS OFF CACHE BOOL "" FORCE)
set(LEVELDB_INSTALL OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_EXAMPLE OFF CACHE BOOL "" FORCE)

add_subdirectory(../third_party/leveldb-1.23 ${CMAKE_BINARY_DIR}/leveldb_build)

if(TARGET leveldb)
  target_compile_options(leveldb PRIVATE -fPIC)
endif()

add_subdirectory(../third_party/spdlog-1.14.1 ${CMAKE_BINARY_DIR}/spdlog_build)

# ARM platform detection and KRL integration
set(OV_PLATFORM_ARM OFF)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|ARM64|arm64")
    set(OV_PLATFORM_ARM ON)
    message(STATUS "Building for ARM platform with KRL support")
    add_subdirectory(../third_party/krl ${CMAKE_BINARY_DIR}/krl_build)
endif()

include_directories(.)
include_directories(../third_party/)
include_directories(../third_party/leveldb-1.23/include/)
include_directories(../third_party/spdlog-1.14.1/include/)

# Add KRL include directory for ARM platform
if(OV_PLATFORM_ARM)
    include_directories(../third_party/krl/include/)
endif()

if(NOT DEFINED Python3_INCLUDE_DIRS)
    set(Python3_INCLUDE_DIRS 
        ${Python3_ARCH_INCLUDE_DIR}
        "/usr/include/../include/"
    )
endif()

file(GLOB_RECURSE ALL_SOURCES
    "index/*.cpp"
    "store/*.cpp"
    "common/*.cpp"
)

add_library(
    engine_impl
    STATIC
    ${ALL_SOURCES}
)

target_link_libraries(engine_impl PRIVATE 
    Threads::Threads
    leveldb
)

# Link KRL library for ARM platform
if(OV_PLATFORM_ARM)
    target_link_libraries(engine_impl PRIVATE krl)
endif()

if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "9.0")
        target_link_libraries(engine_impl PRIVATE stdc++fs)
    endif()
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "10.0")
        target_link_libraries(engine_impl PRIVATE c++fs)
    endif()
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
    if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "11.0")
        target_link_libraries(engine_impl PRIVATE c++fs)
    endif()
endif()

target_compile_options(engine_impl PRIVATE -fPIC)

pybind11_add_module(
    engine
    MODULE
    pybind11_interface.cpp
)

set(EXTENSION_SUFFIX ".so")
if(WIN32)
    set(EXTENSION_SUFFIX ".pyd")
endif()

set_target_properties(
    engine 
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PY_OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${PY_OUTPUT_DIR}"
    SUFFIX "${EXTENSION_SUFFIX}"
    PYBIND11_MODULE_NAME "engine"
    OUTPUT_NAME "engine"
)

target_include_directories(engine PRIVATE
    ${Python3_INCLUDE_DIRS}
)

target_link_libraries(engine PRIVATE 
    engine_impl
    Threads::Threads
)

if(MINGW)
    target_link_libraries(engine PRIVATE 
        -static-libgcc 
        -static-libstdc++ 
        -Wl,-Bstatic 
        -lstdc++ 
        -lpthread 
        -Wl,-Bdynamic
    )
endif()

if(NOT DEFINED PY_OUTPUT_DIR)
    set(PY_OUTPUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/${PY_PACKAGE_NAME})
endif()
