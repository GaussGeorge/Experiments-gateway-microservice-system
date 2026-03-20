## DAGOR Open Source Implementation

### Overview

`dagor-grpc` is a gRPC interceptor library.
This repository contains an open-source implementation of the **DAGOR** overload control mechanism, which was originally proposed in the paper *"Overload Control for Scaling WeChat Microservices"*. We have followed the algorithm described in the paper to build this implementation.

DAGOR is designed to manage overload in microservices by dynamically adjusting request admission levels based on both business and user priorities. It aims to maintain high throughput during overload situations by shedding excess load collaboratively across microservices.

### Thread Safety
In the DAGOR implementation, we use Go's **`sync.Map`** for concurrent data access to shared resources and **atomic operations** for safely updating request counters, admission levels, and overload indicators. This ensures that the system can handle high-concurrency environments typical in microservice architectures without performance degradation due to locking contention.


## Installation

To install the `dagor-grpc` package, run the following command:

```bash
go get -u github.com/pennsail/dagor-grpc
```

Ensure that you have Go installed and your workspace is correctly configured.

## Usage

### Server Interceptor

To use `dagor` as a server interceptor, refer to the following example:

```go
import (
  "github.com/pennsail/dagor-grpc/dagor"
  "google.golang.org/grpc"
)

func main() {
  serverOptions := []grpc.ServerOption{
    grpc.UnaryInterceptor(dagor.UnaryServerInterceptor),
  }

  server := grpc.NewServer(serverOptions...)
  // Register services and start the server
}
```

### Client Interceptor

To integrate `dagor` as a client interceptor, consult the following example:

```go
import (
  "github.com/pennsail/dagor-grpc/dagor"
  "google.golang.org/grpc"
)

func main() {
  clientOptions := []grpc.DialOption{
    grpc.WithUnaryInterceptor(dagor.UnaryClientInterceptor),
  }

  conn, err := grpc.Dial("localhost:50051", clientOptions...)
  // Handle the connection and execute client logic
}
```

## Contributing

Contributions from the community are welcome. For more information, please read the [contribution guidelines](CONTRIBUTING.md).

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
```

You can place this content into your `README.md` file in the root directory of your repository. Feel free to adjust it according to your project's specific requirements.