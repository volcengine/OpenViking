import { useEffect, useRef } from 'react';

/** Cancel field validation when the question instance is replaced. */
export function useValidationLifetime() {
  const validationRequest = useRef(new AbortController());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    validationRequest.current = controller;
    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, []);
  return { validationRequest, mounted };
}
