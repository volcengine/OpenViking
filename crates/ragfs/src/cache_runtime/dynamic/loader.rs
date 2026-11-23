use super::abi::{
    ProviderApiV1, ProviderApiV2, ProviderEntryV1, ProviderEntryV2, ABI_VERSION_V1,
    ABI_VERSION_V2, ENTRY_SYMBOL_V1, ENTRY_SYMBOL_V2,
};
use super::abi::{ProviderCloseV1, ProviderCreateV1, ProviderPingV1};
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

#[repr(C)]
#[derive(Clone, Copy)]
struct ProviderApiRequiredV1 {
    abi_version: u32,
    struct_size: usize,
    create: Option<ProviderCreateV1>,
    ping: Option<ProviderPingV1>,
    close: Option<ProviderCloseV1>,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct ProviderApiRequiredV2 {
    v1: ProviderApiRequiredV1,
}

pub(super) struct LoadedProviderApi {
    pub(super) library: Library,
    pub(super) api: ProviderApiV1,
    pub(super) v2: Option<ProviderApiV2>,
}

/// Load the preferred V2 ABI, falling back only when its symbol is absent.
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
    if let Some(api) = load_v2(&library, path)? {
        return Ok(LoadedProviderApi {
            library,
            api: api.v1,
            v2: Some(api),
        });
    }
    let api = load_v1(&library, path)?;
    Ok(LoadedProviderApi {
        library,
        api,
        v2: None,
    })
}

/// Load and validate the compatible V1 ABI table.
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
    validate_header(
        header,
        ABI_VERSION_V1,
        size_of::<ProviderApiRequiredV1>(),
        path,
    )?;
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

/// Load and validate the V2 ABI table when the provider exports its entry point.
fn load_v2(library: &Library, path: &Path) -> CacheResult<Option<ProviderApiV2>> {
    let entry: Symbol<ProviderEntryV2> = match unsafe { library.get(ENTRY_SYMBOL_V2) } {
        Ok(entry) => entry,
        Err(_) => return Ok(None),
    };
    let api_ptr = unsafe { entry() };
    if api_ptr.is_null() {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} returned a null V2 API table",
            path.display()
        )));
    }
    if !(api_ptr as usize).is_multiple_of(align_of::<ProviderApiV2>()) {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} returned a misaligned V2 API table",
            path.display()
        )));
    }
    let header = unsafe { ptr::read(api_ptr.cast::<ProviderApiHeader>()) };
    validate_header(
        header,
        ABI_VERSION_V2,
        size_of::<ProviderApiRequiredV2>(),
        path,
    )?;
    let mut api = unsafe { std::mem::zeroed::<ProviderApiV2>() };
    let copy_len = header.struct_size.min(size_of::<ProviderApiV2>());
    unsafe {
        ptr::copy_nonoverlapping(
            api_ptr.cast::<u8>(),
            (&mut api as *mut ProviderApiV2).cast::<u8>(),
            copy_len,
        );
    }
    validate_callbacks(&api.v1, path)?;
    Ok(Some(api))
}

/// Validate an ABI table header against the selected version and required prefix.
fn validate_header(
    header: ProviderApiHeader,
    expected_version: u32,
    required_size: usize,
    path: &Path,
) -> CacheResult<()> {
    if header.abi_version != expected_version {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} uses ABI {}, expected {}",
            path.display(),
            header.abi_version,
            expected_version
        )));
    }
    if header.struct_size < required_size {
        return Err(CacheError::AbiMismatch(format!(
            "dynamic provider {} API table is {} bytes, expected at least {}",
            path.display(),
            header.struct_size,
            required_size
        )));
    }
    Ok(())
}

/// Require lifecycle callbacks shared by V1 and V2 provider tables.
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
