package dagor

import (
	"context"
	"errors"
	"strconv"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type ThresholdTable struct {
	BStar int
	UStar int
}

func (d *Dagor) UnaryInterceptorClient(ctx context.Context, method string, req interface{}, reply interface{}, cc *grpc.ClientConn, invoker grpc.UnaryInvoker, opts ...grpc.CallOption) error {
	// if d.isEnduser, attach user id to metadata and send request
	if d.isEnduser {
		ctx = metadata.AppendToOutgoingContext(ctx, "user-id", d.uuid)
		err := invoker(ctx, method, req, reply, cc, opts...)
		if err != nil {
			logger("[End User] %s is an end user, req got error: %v", d.uuid, err)
			return err
		}
		logger("[End User] %s is an end user, req completed", d.uuid)
		return nil
	}

	// Extracting metadata
	md, ok := metadata.FromOutgoingContext(ctx)
	if !ok {
		return errors.New("could not retrieve metadata from context")
	}

	// Extracting method name and determining B value
	methodName, ok := md["method"]
	if !ok || len(methodName) == 0 {
		return errors.New("method name not found in metadata")
	}

	// Check if B and U are in the metadata
	BValues, BExists := md["b"]
	UValues, UExists := md["u"]

	if !BExists || !UExists {
		// if B or U not in metadata, this client is end user, otherwise, fatal error
		if !d.isEnduser {
			logger("[Client Sending Req] not an enduser and B or U not found in metadata, fatal error")
			return status.Errorf(codes.InvalidArgument, "B or U not found in metadata, fatal error")
		}
	}

	var B, U int
	// otherwise, this client is a DAGOR node in the service app
	B, _ = strconv.Atoi(BValues[0])
	U, _ = strconv.Atoi(UValues[0])
	// check if B and U against threshold table before sending sub-request
	// Thresholding

	val, ok := d.thresholdTable.Load(methodName[0])
	if ok {
		threshold := val.(thresholdVal)
		if B > threshold.Bstar || (B == threshold.Bstar && U > threshold.Ustar) {
			logger("[Ratelimiting] B %d or U %d value above the threshold B* %d or U* %d, request dropped", B, U, threshold.Bstar, threshold.Ustar)
			return status.Errorf(codes.ResourceExhausted, "[Local Admission Control] B or U values do not meet the threshold B* or U*, request dropped")
		}
		logger("[Ratelimiting] B %d and U %d values below the threshold B* %d and U* %d, request sent", B, U, threshold.Bstar, threshold.Ustar)
	} else {
		logger("[Ratelimiting] B* and U* values not found in the threshold table for method %s.", methodName[0])
	}

	// Invoking the gRPC call
	var header metadata.MD
	err := invoker(ctx, method, req, reply, cc, grpc.Header(&header))
	if err != nil {
		return err
	}

	// Store received B* and U* values from the header
	BstarValues := header.Get("b-star")
	UstarValues := header.Get("u-star")
	if len(BstarValues) > 0 && len(UstarValues) > 0 {
		Bstar, _ := strconv.Atoi(BstarValues[0])
		Ustar, _ := strconv.Atoi(UstarValues[0])
		d.thresholdTable.Store(methodName[0], thresholdVal{Bstar: Bstar, Ustar: Ustar})
		// d.thresholdTable[methodName[0]] = thresholdVal{Bstar: Bstar, Ustar: Ustar}
		logger("Received B* and U* values from the header: B*=%d, U*=%d", Bstar, Ustar)
	}

	return nil
}
