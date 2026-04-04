cmake_minimum_required(VERSION 3.15)

project(openviking_cpp)

include(CheckCXXCompilerFlag)
include(CMakeParseArguments)

set(OV_X86_BUILD_VARIANTS "sse3;avx2;avx512" CACHE STRING "x86 engine variants to build")
set(OV_PY_OUTPUT_DIR "" CACHE PATH "Output directory for Python extension modules")
set(OV_PY_EXT_SUFFIX ".so" CACHE STRING "Python extension suffix, including ABI tag if needed")
set(OV_PYTHON_SABI_LIBRARY "" CACHE FILEPATH "Stable-ABI Python import library or DLL for Windows abi3 modules")

if(NOT OV_PY_OUTPUT_DIR)
    set(OV_PY_OUTPUT_DIR "${CMAKE_CURRENT_BINARY_DIR}/python_engine")
endif()

set(OV_PLATFORM_X86 OFF)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64|AMD64|i[3-6]86")
    set(OV_PLATFORM_X86 ON)
endif()

set(OV_PLATFORM_ARM OFF)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|ARM64|arm64")
    set(OV_PLATFORM_ARM ON)
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
if(WIN32 AND CMAKE_VERSION VERSION_GREATER_EQUAL "3.26")
    find_package(Python3 COMPONENTS Development.SABIModule QUIET)
endif()

if(UNIX AND NOT APPLE)
    set(Python3_LIBRARIES "")
endif()

find_package(Threads REQUIRED)

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

if(OV_PLATFORM_ARM)
    message(STATUS "Building for ARM platform with KRL support")
    add_subdirectory(../third_party/krl ${CMAKE_BINARY_DIR}/krl_build)
endif()

include_directories(.)
include_directories(../third_party/)
include_directories(../third_party/leveldb-1.23/include/)
include_directories(../third_party/spdlog-1.14.1/include/)

if(OV_PLATFORM_ARM)
    include_directories(../third_party/krl/include/)
endif()

if(NOT DEFINED Python3_INCLUDE_DIRS)
    set(Python3_INCLUDE_DIRS
        ${Python3_ARCH_INCLUDE_DIR}
        "/usr/include/../include/"
    )
endif()

set(OV_COMMON_SOURCES
    common/log_utils.cpp
    store/bytes_row.cpp
    store/persist_store.cpp
    store/volatile_store.cpp
)

set(OV_INDEX_SOURCES
    index/detail/index_manager_impl.cpp
    index/detail/meta/scalar_index_meta.cpp
    index/detail/meta/vector_index_meta.cpp
    index/detail/scalar/bitmap_holder/bitmap.cpp
    index/detail/scalar/bitmap_holder/bitmap_field_group.cpp
    index/detail/scalar/bitmap_holder/dir_index.cpp
    index/detail/scalar/bitmap_holder/ranged_map.cpp
    index/detail/scalar/filter/filter_ops.cpp
    index/detail/scalar/filter/op_base.cpp
    index/detail/scalar/filter/sort_ops.cpp
    index/detail/scalar/scalar_index.cpp
    index/detail/vector/sparse_retrieval/sparse_datapoint.cpp
    index/detail/vector/sparse_retrieval/sparse_row_index.cpp
    index/index_engine.cpp
)

add_library(engine_common STATIC ${OV_COMMON_SOURCES})
target_compile_options(engine_common PRIVATE -fPIC)
target_link_libraries(engine_common PUBLIC Threads::Threads leveldb)

function(ov_link_filesystem_libs target_name)
    if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "9.0")
            target_link_libraries(${target_name} PRIVATE stdc++fs)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
        if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "10.0")
            target_link_libraries(${target_name} PRIVATE c++fs)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
        if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "11.0")
            target_link_libraries(${target_name} PRIVATE c++fs)
        endif()
    endif()
endfunction()

function(ov_get_x86_variant_flags variant out_flags out_defs out_supported)
    string(TOLOWER "${variant}" OV_VARIANT)
    set(OV_FLAGS)
    set(OV_DEFS)
    set(OV_SUPPORTED TRUE)

    if(OV_VARIANT STREQUAL "sse3")
        check_cxx_compiler_flag("-msse3" HAVE_OV_SSE3)
        if(HAVE_OV_SSE3)
            list(APPEND OV_FLAGS -msse3)
            list(APPEND OV_DEFS OV_DISABLE_AVX512=1)
            list(APPEND OV_DEFS CROARING_COMPILER_SUPPORTS_AVX512=0)
            list(APPEND OV_DEFS CROARING_DISABLE_X64=1)
        else()
            set(OV_SUPPORTED FALSE)
        endif()
    elseif(OV_VARIANT STREQUAL "avx2")
        check_cxx_compiler_flag("-mavx2" HAVE_OV_AVX2)
        if(HAVE_OV_AVX2)
            list(APPEND OV_FLAGS -mavx2)
            list(APPEND OV_DEFS OV_DISABLE_AVX512=1)
            list(APPEND OV_DEFS CROARING_COMPILER_SUPPORTS_AVX512=0)
            foreach(FLAG -mno-avx512f -mno-avx512bw -mno-avx512dq -mno-avx512vl)
                string(REPLACE "-" "_" FLAG_VAR_SUFFIX "${FLAG}")
                set(FLAG_VAR "HAVE_${FLAG_VAR_SUFFIX}")
                check_cxx_compiler_flag("${FLAG}" ${FLAG_VAR})
                if(${FLAG_VAR})
                    list(APPEND OV_FLAGS ${FLAG})
                endif()
            endforeach()
        else()
            set(OV_SUPPORTED FALSE)
        endif()
    elseif(OV_VARIANT STREQUAL "avx512")
        foreach(FLAG -mavx512f -mavx512bw -mavx512dq -mavx512vl)
            string(REPLACE "-" "_" FLAG_VAR_SUFFIX "${FLAG}")
            set(FLAG_VAR "HAVE_${FLAG_VAR_SUFFIX}")
            check_cxx_compiler_flag("${FLAG}" ${FLAG_VAR})
            if(NOT ${FLAG_VAR})
                set(OV_SUPPORTED FALSE)
            else()
                list(APPEND OV_FLAGS ${FLAG})
            endif()
        endforeach()
    else()
        set(OV_SUPPORTED FALSE)
    endif()

    set(${out_flags} "${OV_FLAGS}" PARENT_SCOPE)
    set(${out_defs} "${OV_DEFS}" PARENT_SCOPE)
    set(${out_supported} ${OV_SUPPORTED} PARENT_SCOPE)
endfunction()

function(ov_link_python_abi target_name)
    if(WIN32)
        if(OV_PYTHON_SABI_LIBRARY)
            target_link_libraries(${target_name} PRIVATE "${OV_PYTHON_SABI_LIBRARY}")
        elseif(TARGET Python3::SABIModule)
            target_link_libraries(${target_name} PRIVATE Python3::SABIModule)
        else()
            message(
                FATAL_ERROR
                "Windows abi3 modules require OV_PYTHON_SABI_LIBRARY or Python3::SABIModule"
            )
        endif()
    endif()

    if(APPLE)
        target_link_options(${target_name} PRIVATE "-undefined" "dynamic_lookup")
    endif()
endfunction()

function(ov_add_python_backend backend_suffix module_name)
    set(oneValueArgs INDEX_LIBRARY)
    set(multiValueArgs COMPILE_OPTIONS COMPILE_DEFINITIONS)
    cmake_parse_arguments(OV_BACKEND "" "${oneValueArgs}" "${multiValueArgs}" ${ARGN})

    set(MODULE_TARGET "engine_module_${backend_suffix}")
    add_library(${MODULE_TARGET} MODULE abi3_engine_backend.cpp)

    target_include_directories(${MODULE_TARGET} PRIVATE ${Python3_INCLUDE_DIRS})
    target_compile_options(${MODULE_TARGET} PRIVATE -fPIC ${OV_BACKEND_COMPILE_OPTIONS})
    target_compile_definitions(
        ${MODULE_TARGET}
        PRIVATE
            Py_LIMITED_API=0x030A0000
            OV_PY_MODULE_NAME=${module_name}
            ${OV_BACKEND_COMPILE_DEFINITIONS}
    )
    target_link_libraries(
        ${MODULE_TARGET}
        PRIVATE
            engine_common
            ${OV_BACKEND_INDEX_LIBRARY}
            Threads::Threads
    )
    ov_link_python_abi(${MODULE_TARGET})
    ov_link_filesystem_libs(${MODULE_TARGET})

    if(MINGW)
        target_link_libraries(${MODULE_TARGET} PRIVATE
            -static-libgcc
            -static-libstdc++
            -Wl,-Bstatic
            -lstdc++
            -lpthread
            -Wl,-Bdynamic
        )
    endif()

    set_target_properties(
        ${MODULE_TARGET}
        PROPERTIES
            LIBRARY_OUTPUT_DIRECTORY "${OV_PY_OUTPUT_DIR}"
            RUNTIME_OUTPUT_DIRECTORY "${OV_PY_OUTPUT_DIR}"
            PREFIX ""
            OUTPUT_NAME "${module_name}"
            SUFFIX "${OV_PY_EXT_SUFFIX}"
    )
endfunction()

set(OV_ENGINE_IMPL_TARGET "")

if(OV_PLATFORM_X86)
    set(OV_BUILT_X86_VARIANTS)

    foreach(OV_VARIANT IN LISTS OV_X86_BUILD_VARIANTS)
        ov_get_x86_variant_flags("${OV_VARIANT}" OV_VARIANT_FLAGS OV_VARIANT_DEFS OV_VARIANT_SUPPORTED)
        if(NOT OV_VARIANT_SUPPORTED)
            message(STATUS "Skipping unsupported x86 engine variant: ${OV_VARIANT}")
            continue()
        endif()

        set(INDEX_TARGET "engine_index_${OV_VARIANT}")
        add_library(${INDEX_TARGET} STATIC ${OV_INDEX_SOURCES})
        target_compile_options(${INDEX_TARGET} PRIVATE -fPIC ${OV_VARIANT_FLAGS})
        target_compile_definitions(${INDEX_TARGET} PRIVATE ${OV_VARIANT_DEFS})
        target_link_libraries(${INDEX_TARGET} PUBLIC Threads::Threads)

        ov_add_python_backend(
            "${OV_VARIANT}"
            "_x86_${OV_VARIANT}"
            INDEX_LIBRARY ${INDEX_TARGET}
            COMPILE_OPTIONS ${OV_VARIANT_FLAGS}
            COMPILE_DEFINITIONS ${OV_VARIANT_DEFS}
        )

        list(APPEND OV_BUILT_X86_VARIANTS "${OV_VARIANT}")
    endforeach()

    add_library(engine_module_x86_caps MODULE abi3_x86_caps.cpp)
    target_include_directories(engine_module_x86_caps PRIVATE ${Python3_INCLUDE_DIRS})
    target_compile_definitions(engine_module_x86_caps PRIVATE Py_LIMITED_API=0x030A0000)
    ov_link_python_abi(engine_module_x86_caps)
    set_target_properties(
        engine_module_x86_caps
        PROPERTIES
            LIBRARY_OUTPUT_DIRECTORY "${OV_PY_OUTPUT_DIR}"
            RUNTIME_OUTPUT_DIRECTORY "${OV_PY_OUTPUT_DIR}"
            PREFIX ""
            OUTPUT_NAME "_x86_caps"
            SUFFIX "${OV_PY_EXT_SUFFIX}"
    )

    if(TARGET engine_index_sse3)
        add_library(engine_impl INTERFACE)
        target_link_libraries(engine_impl INTERFACE engine_common engine_index_sse3 Threads::Threads)
        if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
            if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "9.0")
                target_link_libraries(engine_impl INTERFACE stdc++fs)
            endif()
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
            if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "10.0")
                target_link_libraries(engine_impl INTERFACE c++fs)
            endif()
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
            if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "11.0")
                target_link_libraries(engine_impl INTERFACE c++fs)
            endif()
        endif()
        set(OV_ENGINE_IMPL_TARGET "engine_impl")
    endif()

    message(STATUS "OpenViking x86 engine variants: ${OV_BUILT_X86_VARIANTS}")
else()
    add_library(engine_index_native STATIC ${OV_INDEX_SOURCES})
    target_compile_options(engine_index_native PRIVATE -fPIC)
    target_link_libraries(engine_index_native PUBLIC Threads::Threads)
    if(OV_PLATFORM_ARM)
        target_link_libraries(engine_index_native PUBLIC krl)
    endif()

    ov_add_python_backend("native" "_native" INDEX_LIBRARY engine_index_native)

    add_library(engine_impl INTERFACE)
    target_link_libraries(engine_impl INTERFACE engine_common engine_index_native Threads::Threads)
    if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "9.0")
            target_link_libraries(engine_impl INTERFACE stdc++fs)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
        if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "10.0")
            target_link_libraries(engine_impl INTERFACE c++fs)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
        if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "11.0")
            target_link_libraries(engine_impl INTERFACE c++fs)
        endif()
    endif()
    set(OV_ENGINE_IMPL_TARGET "engine_impl")

    message(STATUS "OpenViking native engine backend: _native")
endif()
