/// Map a PathLockError to the appropriate Python exception.
fn pathlock_err_to_py(err: PathLockError) -> PyErr {
    match &err {
        PathLockError::HandoffRecoveryRequired { reason } => Python::attach(|py| {
            let ty = HANDOFF_RECOVERY_ERROR_TYPE
                .get()
                .expect("HandoffRecoveryRequired not initialized");
            PyErr::from_type(ty.bind(py).clone(), reason.to_string())
        }),
        PathLockError::Conflict { .. }
        | PathLockError::Timeout { .. }
        | PathLockError::HandoffFailed(_)
        | PathLockError::Busy { .. }
        | PathLockError::EmptyToken { .. } =>
        {
            #[allow(deprecated)]
            Python::with_gil(|py| {
                let ty = LOCK_ACQUISITION_ERROR_TYPE
                    .get()
                    .expect("LockAcquisitionError not initialized");
                let exception = PyErr::from_type(ty.bind(py).clone(), err.to_string());
                if let PathLockError::Timeout {
                    conflicting_owner: Some(owner),
                    ..
                } = &err
                {
                    if let Err(error) = exception.value(py).setattr("conflicting_owner", owner) {
                        return error;
                    }
                }
                exception
            })
        }
        PathLockError::InvalidRequest(_) => PyValueError::new_err(err.to_string()),
        PathLockError::Io(_) | PathLockError::InvalidToken(_) | PathLockError::Internal(_) => {
            new_py_err("AGFSInternalError", err.to_string())
        }
    }
}
