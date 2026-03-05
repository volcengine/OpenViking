module github.com/dongxuny/agfs-fuse

go 1.19

require github.com/c4pt0r/agfs/agfs-sdk/go v0.0.0-00010101000000-000000000000

require (
	github.com/hanwen/go-fuse/v2 v2.9.0
	github.com/sirupsen/logrus v1.9.3
	golang.org/x/sys v0.28.0 // indirect
)

replace github.com/c4pt0r/agfs/agfs-sdk/go => ../agfs-sdk/go
