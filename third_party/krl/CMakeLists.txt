cmake_minimum_required(VERSION 3.12)

project(krl CXX)

# Only build on ARM platform
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|ARM64|arm64")
    # Set C++ standard
    set(CMAKE_CXX_STANDARD 17)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    
    # ARM compile options - use more conservative flags
    add_compile_options(-O2 -fPIC -fvisibility=hidden)
    
    # Minimal set for OpenViking: only krl_L2sqr and krl_ipdis (float, single-vector)
    # C++ sources following OpenViking code style
    set(KRL_SOURCES
        ${CMAKE_CURRENT_SOURCE_DIR}/src/L2distance_simd.cpp
        ${CMAKE_CURRENT_SOURCE_DIR}/src/IPdistance_simd.cpp
    )
    
    # Create static library
    add_library(krl STATIC ${KRL_SOURCES})
    
    # Include directories
    target_include_directories(krl PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    
    # PIC for static library
    set_target_properties(krl PROPERTIES POSITION_INDEPENDENT_CODE ON)
    
    message(STATUS "KRL library configured for ARM platform (core distance functions only)")
else()
    message(STATUS "KRL library skipped - not ARM platform")
endif()
