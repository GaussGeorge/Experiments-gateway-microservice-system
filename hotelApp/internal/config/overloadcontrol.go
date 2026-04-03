package config

import (
	"context"
	"fmt"
	"strings"
	"sync/atomic"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
)

// grpcMethodToBusinessMethod maps gRPC method names (from info.FullMethod) to
// the business method names expected by OC gateways (dagor businessMap, topdown sloMap, etc.).
var grpcMethodToBusinessMethod = map[string]string{
	// Frontend service
	"SearchHotels":        "search-hotel",
	"FrontendReservation": "reserve-hotel",
	"StoreHotel":          "store-hotel",
	// Backend services (fallback if PropagateMetadata didn't carry method)
	"Nearby":             "search-hotel",
	"StoreHotelLocation": "store-hotel",
	"GetRates":           "search-hotel",
	"StoreRate":          "store-hotel",
	"GetProfiles":        "search-hotel",
	"StoreProfile":       "store-hotel",
	"Login":              "reserve-hotel",
	"RegisterUser":       "reserve-hotel",
	"CheckAvailability":  "reserve-hotel",
	"MakeReservation":    "reserve-hotel",
	"AddHotelAvailability": "store-hotel",
}

var ocRequestCounter int64

// extractBusinessMethod converts a gRPC info.FullMethod (e.g. "/hotelproto.FrontendService/SearchHotels")
// into a business method name (e.g. "search-hotel").
func extractBusinessMethod(fullMethod string) string {
	parts := strings.Split(fullMethod, "/")
	if len(parts) >= 3 {
		grpcMethod := parts[2]
		if biz, ok := grpcMethodToBusinessMethod[grpcMethod]; ok {
			return biz
		}
		return grpcMethod
	}
	return fullMethod
}

// WrapServerInterceptor wraps a gateway's gRPC server interceptor to inject
// metadata keys that external clients (e.g. ghz load generator) don't provide.
//
// Injected metadata (only if not already present):
//   - "method": business method name derived from info.FullMethod
//   - "tokens": default token budget for rajomon load shedding
//   - "user-id": generated user ID for dagor entry-service priority assignment
func WrapServerInterceptor(inner grpc.UnaryServerInterceptor) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		methodName := extractBusinessMethod(info.FullMethod)

		md, ok := metadata.FromIncomingContext(ctx)
		if !ok {
			md = metadata.New(nil)
		}
		md = md.Copy()

		if _, exists := md["method"]; !exists {
			md.Set("method", methodName)
		}
		if _, exists := md["tokens"]; !exists {
			md.Set("tokens", "1000000")
		}
		if _, exists := md["user-id"]; !exists {
			id := atomic.AddInt64(&ocRequestCounter, 1)
			md.Set("user-id", fmt.Sprintf("user-%d", id%500))
		}

		ctx = metadata.NewIncomingContext(ctx, md)
		return inner(ctx, req, info, handler)
	}
}
