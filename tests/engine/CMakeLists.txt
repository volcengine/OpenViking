cmake_minimum_required(VERSION 3.12)
project(engine_tests)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -g -Wno-error")

include_directories(${CMAKE_SOURCE_DIR}/../../src)
include_directories(${CMAKE_SOURCE_DIR}/../../third_party)
include_directories(${CMAKE_SOURCE_DIR}/../../third_party/spdlog-1.14.1/include)

find_package(Threads REQUIRED)

add_executable(test_common
    test_common.cpp
    ${CMAKE_SOURCE_DIR}/../../src/common/log_utils.cpp
)

target_link_libraries(test_common PRIVATE
    Threads::Threads
)

add_executable(test_index_engine
    test_index_engine.cpp
)

if(NOT TARGET engine_impl)
    message(FATAL_ERROR "engine_impl compatibility target was not created")
endif()

target_link_libraries(test_index_engine PRIVATE
    engine_impl
    Threads::Threads
)
