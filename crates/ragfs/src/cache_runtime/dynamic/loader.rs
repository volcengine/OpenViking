use super::abi::{ProviderApiV1, ProviderEntryV1, ABI_VERSION_V1, ENTRY_SYMBOL_V1};
use crate::cache_runtime::{CacheError, CacheResult};
use libloading::{Library, Symbol};
use std::mem::{align_of, size_of};
use std::path::Path;
use std::ptr;

#[repr(C)]
#[derive(Clone, Copy)]
struct ProviderApiHeaderV1 {
    abi_version: u32,
    struct_size: usize,
}

pub(super) struct LoadedProviderApi {
    pub(super) library: Library,
    pub(super) api: ProviderApiV1,
}

pub(super) fn load(path: &Path) -> CacheResult<LoadedProviderApi> {
    if !path.is_absolute() {
        return Err(CacheError::InvalidArgument(format!(
            "dynamic provider library path must be absolute: {}",
            path.display()
        )));
    }
    let library = unsafe { Library::new(path) }.map_err(|error| {
        CacheError::Unavailable(format!(
            "failed to load dynamic provider {}: {error}",
            path.display()
        ))
    })?;
    let entry: Symbol<ProviderEntryV1> =
        unsafe { library.get(ENTRY_SYMBOL_V1) }.map_err(|error| {
            CacheError::AbiMismatch(format!(
                "dynamic provider {} does not export openviking_cache_provider_v1: {error}",
                path.display()
            ))
        })?;
    let api_ptr = unsafe { entry() };
    if api_ptr.is_null() {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} returned a null API table",
            path.display()
        )));
    }
    if !(api_ptr as usize).is_multiple_of(align_of::<ProviderApiV1>()) {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} returned a misaligned API table",
            path.display()
        )));
    }
    let header = unsafe { ptr::read(api_ptr.cast::<ProviderApiHeaderV1>()) };
    validate_header(header, path)?;
    let api = unsafe { ptr::read(api_ptr) };
    validate_callbacks(&api, path)?;
    Ok(LoadedProviderApi { library, api })
}

fn validate_header(header: ProviderApiHeaderV1, path: &Path) -> CacheResult<()> {
    if header.abi_version != ABI_VERSION_V1 {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} uses ABI {}, expected {}",
            path.display(),
            header.abi_version,
            ABI_VERSION_V1
        )));
    }
    if header.struct_size < size_of::<ProviderApiV1>() {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} API table is {} bytes, expected at least {}",
            path.display(),
            header.struct_size,
            size_of::<ProviderApiV1>()
        )));
    }
    Ok(())
}

fn validate_callbacks(api: &ProviderApiV1, path: &Path) -> CacheResult<()> {
    for (name, present) in [
        ("create", api.create.is_some()),
        ("ping", api.ping.is_some()),
        ("close", api.close.is_some()),
    ] {
        if !present {
            return Err(CacheError::AbiMismatch(format!(
                "dynamic provider {} is missing required {name} callback",
                path.display()
            )));
        }
    }
    Ok(())
}
