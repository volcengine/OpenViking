import { createContext, useContext } from 'react'

const ReadOnlyContext = createContext(false)
export const ReadOnlyProvider = ReadOnlyContext.Provider
export const useReadOnly = () => useContext(ReadOnlyContext)
