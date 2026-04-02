module github.com/Jiali-Xing/hotelApp

go 1.23.0

require (
	github.com/Jiali-Xing/breakwater-grpc v0.0.0-20241011205119-fc6eaf67a5d2
	github.com/Jiali-Xing/dagor-grpc v0.0.0-20241012211944-ddbe9823e0c9
	github.com/Jiali-Xing/hotelproto v0.0.0-20240609022535-ea9137c9f3e8
	github.com/Jiali-Xing/plain v0.0.0-20231227034046-b79fd58cb753
	github.com/Jiali-Xing/rajomon v0.0.0-20250103031846-36769a5569cc
	github.com/Jiali-Xing/socialproto v0.0.0-20240621081102-966a8e827703
	github.com/Jiali-Xing/topdown-grpc v0.0.0-20241013010456-81bb3a39e4f0
	github.com/go-redis/redis/v8 v8.11.5
	github.com/lithammer/shortuuid v3.0.0+incompatible
	github.com/valyala/fastrand v1.1.0
	google.golang.org/grpc v1.70.0
	gopkg.in/yaml.v2 v2.4.0
)

require (
	github.com/bytedance/gopkg v0.1.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/google/uuid v1.6.0 // indirect
	golang.org/x/net v0.35.0 // indirect
	golang.org/x/sys v0.30.0 // indirect
	golang.org/x/text v0.22.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20250227231956-55c901821b1e // indirect
	google.golang.org/protobuf v1.36.5 // indirect
)

// Use local gateway modules from the same repository
replace (
	github.com/Jiali-Xing/breakwater-grpc => ../breakwater-grpc-main
	github.com/Jiali-Xing/dagor-grpc => ../dagor-grpc-main
	github.com/Jiali-Xing/rajomon => ../rajomon-main
	github.com/Jiali-Xing/topdown-grpc => ../topdown-grpc-main
)
