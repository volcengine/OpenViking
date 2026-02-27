package main

/*
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
*/
import "C"

import (
	"encoding/json"
	"fmt"
	"sync"
	"time"
	"unsafe"

	"github.com/c4pt0r/agfs/agfs-server/pkg/filesystem"
	"github.com/c4pt0r/agfs/agfs-server/pkg/mountablefs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin/api"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin/loader"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/gptfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/heartbeatfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/hellofs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/httpfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/kvfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/localfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/memfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/queuefs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/s3fs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/serverinfofs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/sqlfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/streamfs"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugins/streamrotatefs"
)

var (
	globalFS      *mountablefs.MountableFS
	globalFSMu    sync.RWMutex
	handleMap     = make(map[int64]filesystem.FileHandle)
	handleMapMu   sync.RWMutex
	handleIDGen   int64
	errorBuffer   = make(map[int64]string)
	errorBufferMu sync.RWMutex
	errorIDGen    int64
)

func init() {
	poolConfig := api.PoolConfig{
		MaxInstances: 10,
	}
	globalFS = mountablefs.NewMountableFS(poolConfig)
	registerBuiltinPlugins()
}

func registerBuiltinPlugins() {
	registerFunc := func(name string, factory func() plugin.ServicePlugin) {
		globalFS.RegisterPluginFactory(name, factory)
	}

	registerFunc("serverinfofs", func() plugin.ServicePlugin { return serverinfofs.NewServerInfoFSPlugin() })
	registerFunc("memfs", func() plugin.ServicePlugin { return memfs.NewMemFSPlugin() })
	registerFunc("queuefs", func() plugin.ServicePlugin { return queuefs.NewQueueFSPlugin() })
	registerFunc("kvfs", func() plugin.ServicePlugin { return kvfs.NewKVFSPlugin() })
	registerFunc("hellofs", func() plugin.ServicePlugin { return hellofs.NewHelloFSPlugin() })
	registerFunc("heartbeatfs", func() plugin.ServicePlugin { return heartbeatfs.NewHeartbeatFSPlugin() })
	registerFunc("httpfs", func() plugin.ServicePlugin { return httpfs.NewHTTPFSPlugin() })
	registerFunc("s3fs", func() plugin.ServicePlugin { return s3fs.NewS3FSPlugin() })
	registerFunc("streamfs", func() plugin.ServicePlugin { return streamfs.NewStreamFSPlugin() })
	registerFunc("streamrotatefs", func() plugin.ServicePlugin { return streamrotatefs.NewStreamRotateFSPlugin() })
	registerFunc("sqlfs", func() plugin.ServicePlugin { return sqlfs.NewSQLFSPlugin() })
	registerFunc("localfs", func() plugin.ServicePlugin { return localfs.NewLocalFSPlugin() })
	registerFunc("gptfs", func() plugin.ServicePlugin { return gptfs.NewGptfs() })
}

func storeError(err error) int64 {
	if err == nil {
		return 0
	}
	errorBufferMu.Lock()
	errorIDGen++
	id := errorIDGen
	errorBuffer[id] = err.Error()
	errorBufferMu.Unlock()
	return id
}

func getAndClearError(id int64) string {
	if id == 0 {
		return ""
	}
	errorBufferMu.Lock()
	msg := errorBuffer[id]
	delete(errorBuffer, id)
	errorBufferMu.Unlock()
	return msg
}

func storeHandle(handle filesystem.FileHandle) int64 {
	handleMapMu.Lock()
	handleIDGen++
	id := handleIDGen
	handleMap[id] = handle
	handleMapMu.Unlock()
	return id
}

func getHandle(id int64) filesystem.FileHandle {
	handleMapMu.RLock()
	handle := handleMap[id]
	handleMapMu.RUnlock()
	return handle
}

func removeHandle(id int64) {
	handleMapMu.Lock()
	delete(handleMap, id)
	handleMapMu.Unlock()
}

//export AGFS_NewClient
func AGFS_NewClient() int64 {
	return 1
}

//export AGFS_FreeClient
func AGFS_FreeClient(clientID int64) {
}

//export AGFS_GetLastError
func AGFS_GetLastError(errorID int64) *C.char {
	msg := getAndClearError(errorID)
	return C.CString(msg)
}

//export AGFS_FreeString
func AGFS_FreeString(s *C.char) {
	C.free(unsafe.Pointer(s))
}

//export AGFS_Health
func AGFS_Health(clientID int64) C.int {
	return C.int(1)
}

//export AGFS_GetCapabilities
func AGFS_GetCapabilities(clientID int64) *C.char {
	caps := map[string]interface{}{
		"version":  "binding",
		"features": []string{"handlefs", "grep", "digest", "stream", "touch"},
	}
	data, _ := json.Marshal(caps)
	return C.CString(string(data))
}

//export AGFS_Ls
func AGFS_Ls(clientID int64, path *C.char) *C.char {
	p := C.GoString(path)
	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	files, err := fs.ReadDir(p)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	result := make([]map[string]interface{}, len(files))
	for i, f := range files {
		result[i] = map[string]interface{}{
			"name":    f.Name,
			"size":    f.Size,
			"mode":    f.Mode,
			"modTime": f.ModTime.Format(time.RFC3339Nano),
			"isDir":   f.IsDir,
		}
	}

	data, _ := json.Marshal(map[string]interface{}{"files": result})
	return C.CString(string(data))
}

//export AGFS_Read
func AGFS_Read(clientID int64, path *C.char, offset C.int64_t, size C.int64_t, outData **C.char, outSize *C.int64_t) C.int64_t {
	p := C.GoString(path)
	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	data, err := fs.Read(p, int64(offset), int64(size))
	if err != nil && err.Error() != "EOF" {
		errorID := storeError(err)
		return C.int64_t(errorID)
	}

	if len(data) > 0 {
		buf := C.malloc(C.size_t(len(data)))
		C.memcpy(buf, unsafe.Pointer(&data[0]), C.size_t(len(data)))
		*outData = (*C.char)(buf)
		*outSize = C.int64_t(len(data))
	} else {
		*outData = nil
		*outSize = 0
	}
	return 0
}

//export AGFS_Write
func AGFS_Write(clientID int64, path *C.char, data unsafe.Pointer, dataSize C.int64_t) *C.char {
	p := C.GoString(path)
	bytesData := C.GoBytes(data, C.int(dataSize))

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	n, err := fs.Write(p, bytesData, -1, filesystem.WriteFlagCreate|filesystem.WriteFlagTruncate)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(fmt.Sprintf(`{"message": "Written %d bytes"}`, n))
}

//export AGFS_Create
func AGFS_Create(clientID int64, path *C.char) *C.char {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	err := fs.Create(p)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "file created"}`)
}

//export AGFS_Mkdir
func AGFS_Mkdir(clientID int64, path *C.char, mode C.uint) *C.char {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	err := fs.Mkdir(p, uint32(mode))
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "directory created"}`)
}

//export AGFS_Rm
func AGFS_Rm(clientID int64, path *C.char, recursive C.int) *C.char {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	var err error
	if recursive != 0 {
		err = fs.RemoveAll(p)
	} else {
		err = fs.Remove(p)
	}

	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "deleted"}`)
}

//export AGFS_Stat
func AGFS_Stat(clientID int64, path *C.char) *C.char {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	info, err := fs.Stat(p)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	result := map[string]interface{}{
		"name":    info.Name,
		"size":    info.Size,
		"mode":    info.Mode,
		"modTime": info.ModTime.Format(time.RFC3339Nano),
		"isDir":   info.IsDir,
	}

	data, _ := json.Marshal(result)
	return C.CString(string(data))
}

//export AGFS_Mv
func AGFS_Mv(clientID int64, oldPath *C.char, newPath *C.char) *C.char {
	oldP := C.GoString(oldPath)
	newP := C.GoString(newPath)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	err := fs.Rename(oldP, newP)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "renamed"}`)
}

//export AGFS_Chmod
func AGFS_Chmod(clientID int64, path *C.char, mode C.uint) *C.char {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	err := fs.Chmod(p, uint32(mode))
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "permissions changed"}`)
}

//export AGFS_Touch
func AGFS_Touch(clientID int64, path *C.char) *C.char {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	err := fs.Touch(p)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "touched"}`)
}

//export AGFS_Mounts
func AGFS_Mounts(clientID int64) *C.char {
	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	mounts := fs.GetMounts()
	result := make([]map[string]interface{}, len(mounts))
	for i, m := range mounts {
		result[i] = map[string]interface{}{
			"path":   m.Path,
			"fstype": m.Plugin.Name(),
		}
	}

	data, _ := json.Marshal(map[string]interface{}{"mounts": result})
	return C.CString(string(data))
}

//export AGFS_Mount
func AGFS_Mount(clientID int64, fstype *C.char, path *C.char, configJSON *C.char) *C.char {
	fsType := C.GoString(fstype)
	p := C.GoString(path)
	cfgJSON := C.GoString(configJSON)

	var config map[string]interface{}
	if err := json.Unmarshal([]byte(cfgJSON), &config); err != nil {
		config = make(map[string]interface{})
	}

	globalFSMu.Lock()
	fs := globalFS
	globalFSMu.Unlock()

	err := fs.MountPlugin(fsType, p, config)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(fmt.Sprintf(`{"message": "mounted %s at %s"}`, fsType, p))
}

//export AGFS_Unmount
func AGFS_Unmount(clientID int64, path *C.char) *C.char {
	p := C.GoString(path)

	globalFSMu.Lock()
	fs := globalFS
	globalFSMu.Unlock()

	err := fs.Unmount(p)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "unmounted"}`)
}

//export AGFS_LoadPlugin
func AGFS_LoadPlugin(clientID int64, libraryPath *C.char) *C.char {
	libPath := C.GoString(libraryPath)

	globalFSMu.Lock()
	fs := globalFS
	globalFSMu.Unlock()

	p, err := fs.LoadExternalPlugin(libPath)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(fmt.Sprintf(`{"message": "loaded plugin %s", "name": "%s"}`, libPath, p.Name()))
}

//export AGFS_UnloadPlugin
func AGFS_UnloadPlugin(clientID int64, libraryPath *C.char) *C.char {
	libPath := C.GoString(libraryPath)

	globalFSMu.Lock()
	fs := globalFS
	globalFSMu.Unlock()

	err := fs.UnloadExternalPlugin(libPath)
	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "unloaded plugin"}`)
}

//export AGFS_ListPlugins
func AGFS_ListPlugins(clientID int64) *C.char {
	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	plugins := fs.GetLoadedExternalPlugins()
	data, _ := json.Marshal(map[string]interface{}{"loaded_plugins": plugins})
	return C.CString(string(data))
}

//export AGFS_OpenHandle
func AGFS_OpenHandle(clientID int64, path *C.char, flags C.int, mode C.uint, lease C.int) C.int64_t {
	p := C.GoString(path)

	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	handle, err := fs.OpenHandle(p, filesystem.OpenFlag(flags), uint32(mode))
	if err != nil {
		storeError(err)
		return -1
	}

	id := storeHandle(handle)
	return C.int64_t(id)
}

//export AGFS_CloseHandle
func AGFS_CloseHandle(handleID C.int64_t) *C.char {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		return C.CString(`{"error_id": 0}`)
	}

	err := handle.Close()
	removeHandle(id)

	if err != nil {
		errorID := storeError(err)
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, errorID))
	}

	return C.CString(`{"message": "handle closed"}`)
}

//export AGFS_HandleRead
func AGFS_HandleRead(handleID C.int64_t, size C.int64_t, offset C.int64_t, hasOffset C.int) (*C.char, C.int64_t, C.int64_t) {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		errJSON := fmt.Sprintf(`{"error_id": %d}`, storeError(fmt.Errorf("handle not found")))
		return C.CString(errJSON), 0, -1
	}

	buf := make([]byte, int(size))
	var n int
	var err error

	if hasOffset != 0 {
		n, err = handle.ReadAt(buf, int64(offset))
	} else {
		n, err = handle.Read(buf)
	}

	if err != nil && err.Error() != "EOF" {
		errJSON := fmt.Sprintf(`{"error_id": %d}`, storeError(err))
		return C.CString(errJSON), 0, -1
	}

	return C.CString(string(buf[:n])), C.int64_t(n), 0
}

//export AGFS_HandleWrite
func AGFS_HandleWrite(handleID C.int64_t, data unsafe.Pointer, dataSize C.int64_t, offset C.int64_t, hasOffset C.int) *C.char {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(fmt.Errorf("handle not found"))))
	}

	bytesData := C.GoBytes(data, C.int(dataSize))
	var n int
	var err error

	if hasOffset != 0 {
		n, err = handle.WriteAt(bytesData, int64(offset))
	} else {
		n, err = handle.Write(bytesData)
	}

	if err != nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(err)))
	}

	return C.CString(fmt.Sprintf(`{"bytes_written": %d}`, n))
}

//export AGFS_HandleSeek
func AGFS_HandleSeek(handleID C.int64_t, offset C.int64_t, whence C.int) *C.char {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(fmt.Errorf("handle not found"))))
	}

	newPos, err := handle.Seek(int64(offset), int(whence))
	if err != nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(err)))
	}

	return C.CString(fmt.Sprintf(`{"position": %d}`, newPos))
}

//export AGFS_HandleSync
func AGFS_HandleSync(handleID C.int64_t) *C.char {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(fmt.Errorf("handle not found"))))
	}

	err := handle.Sync()
	if err != nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(err)))
	}

	return C.CString(`{"message": "synced"}`)
}

//export AGFS_HandleStat
func AGFS_HandleStat(handleID C.int64_t) *C.char {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(fmt.Errorf("handle not found"))))
	}

	info, err := handle.Stat()
	if err != nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(err)))
	}

	result := map[string]interface{}{
		"name":    info.Name,
		"size":    info.Size,
		"mode":    info.Mode,
		"modTime": info.ModTime.Format(time.RFC3339Nano),
		"isDir":   info.IsDir,
	}

	data, _ := json.Marshal(result)
	return C.CString(string(data))
}

//export AGFS_ListHandles
func AGFS_ListHandles(clientID int64) *C.char {
	handleMapMu.RLock()
	handles := make([]map[string]interface{}, 0, len(handleMap))
	for id, h := range handleMap {
		handles = append(handles, map[string]interface{}{
			"handle_id": id,
			"path":      h.Path(),
		})
	}
	handleMapMu.RUnlock()

	data, _ := json.Marshal(map[string]interface{}{"handles": handles})
	return C.CString(string(data))
}

//export AGFS_GetHandleInfo
func AGFS_GetHandleInfo(handleID C.int64_t) *C.char {
	id := int64(handleID)
	handle := getHandle(id)
	if handle == nil {
		return C.CString(fmt.Sprintf(`{"error_id": %d}`, storeError(fmt.Errorf("handle not found"))))
	}

	result := map[string]interface{}{
		"handle_id": id,
		"path":      handle.Path(),
		"flags":     int(handle.Flags()),
	}

	data, _ := json.Marshal(result)
	return C.CString(string(data))
}

//export AGFS_GetPluginLoader
func AGFS_GetPluginLoader() unsafe.Pointer {
	globalFSMu.RLock()
	fs := globalFS
	globalFSMu.RUnlock()

	l := fs.GetPluginLoader()
	return unsafe.Pointer(l)
}

func GetMountableFS() *mountablefs.MountableFS {
	globalFSMu.RLock()
	defer globalFSMu.RUnlock()
	return globalFS
}

func SetMountableFS(fs *mountablefs.MountableFS) {
	globalFSMu.Lock()
	globalFS = fs
	globalFSMu.Unlock()
}

func GetPluginLoaderInternal() *loader.PluginLoader {
	return globalFS.GetPluginLoader()
}

func main() {}
