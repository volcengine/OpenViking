use super::abi::{ProviderApiV1, ProviderEntryV1, ABI_VERSION_V1, ENTRY_SYMBOL_V1};
use crate::cache_runtime::{CacheError, CacheResult};
use libloading::{Library, Symbol};
use std::mem::{align_of, size_of};
use std::path::Path;
use std::ptr;

#[repr(C)]
#[derive(Clone, Copy)]
struct ProviderApiHeader {
    abi_version: u32,
    struct_size: usize,
}

pub(super) struct LoadedProviderApi {
    pub(super) library: Library,
    pub(super) api: ProviderApiV1,
}

/// Load and validate the breaking V1 ABI table.
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
    let api = load_v1(&library, path)?;
    Ok(LoadedProviderApi { library, api })
}

/// Load one V1 table after requiring its complete breaking layout.
fn load_v1(library: &Library, path: &Path) -> CacheResult<ProviderApiV1> {
    let entry: Symbol<ProviderEntryV1> = unsafe { library.get(ENTRY_SYMBOL_V1) }.map_err(|error| {
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
    let header = unsafe { ptr::read(api_ptr.cast::<ProviderApiHeader>()) };
    validate_header(header, path)?;
    let mut api = unsafe { std::mem::zeroed::<ProviderApiV1>() };
    let copy_len = header.struct_size.min(size_of::<ProviderApiV1>());
    unsafe {
        ptr::copy_nonoverlapping(
            api_ptr.cast::<u8>(),
            (&mut api as *mut ProviderApiV1).cast::<u8>(),
            copy_len,
        );
    }
    validate_callbacks(&api, path)?;
    Ok(api)
}

/// Reject legacy V1 tables that do not contain the new operation fields.
fn validate_header(header: ProviderApiHeader, path: &Path) -> CacheResult<()> {
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

/// Require lifecycle callbacks in the V1 provider table.
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
