cmake_minimum_required(VERSION 3.12)

project(openviking_cpp)

include(CheckCXXCompilerFlag)
check_cxx_compiler_flag("-march=native" HAVE_MARCH_NATIVE)
if(HAVE_MARCH_NATIVE)
    add_compile_options(-march=native)
else()
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64|AMD64")
        check_cxx_compiler_flag("-msse3" HAVE_SSE3)
        if(HAVE_SSE3)
            add_compile_options(-msse3)
        endif()
    endif()
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

include_directories(.)
include_directories(../third_party/)
include_directories(../third_party/leveldb-1.23/include/)
include_directories(../third_party/spdlog-1.14.1/include/)

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
