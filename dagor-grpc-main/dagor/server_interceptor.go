package dagor

import (
	"context"
	"math/rand"
	"runtime/metrics"
	"strconv"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func (d *Dagor) UnaryInterceptorServer(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
	md, _ := metadata.FromIncomingContext(ctx)
	methodNames, methodExists := md["method"]
	// Ensure method name is present
	if !methodExists || len(methodNames) == 0 {
		return nil, status.Errorf(codes.InvalidArgument, "Method name not provided in metadata")
	}
	userIDs, userIDExists := md["user-id"]
	var B, U int
	var err error

	// if this is an entry service, B and U are not in metadata
	if d.entryService {
		methodName := methodNames[0]
		if businessValue, exists := d.businessMap[methodName]; exists {
			B = businessValue
			logger("[Entry service] Entry service found Business value %d for method %s", B, methodName)
		} else {
			// can't find the business value from businessMap
			// return nil, status.Errorf(codes.Internal, "Business value for method %s not found", methodName)
			// assign a random business value between 1 and Bmax
			B = rand.Intn(d.Bmax) + 1
			// make sure the business value is not yet assigned
			for _, v := range d.businessMap {
				if v == B {
					B = rand.Intn(d.Bmax) + 1
				}
			}
			d.businessMap[methodName] = B
			logger("[Entry service] Entry service can't find Business value for method %s, assigned a random value %d", methodName, B)
		}
		if userIDExists && len(userIDs) > 0 {
			userID := userIDs[0]
			if val, ok := d.userPriority.Load(userID); ok {
				U = val.(int)
				logger("[Entry service] User %s already has a priority value assigned: %d", userID, U)
			} else {
				// Assign a random int for U between 1 and Umax
				U = rand.Intn(d.Umax) + 1
				d.userPriority.Store(userID, U)
				logger("User %s assigned a priority value: %d", userID, U)
			}
		} else {
			return nil, status.Errorf(codes.InvalidArgument, "User ID not provided in metadata")
		}
		logger("[Entry service] %s assigned user B: %d, U: %d", d.nodeName, B, U)
		// Modify ctx with the B and U
		ctx = metadata.AppendToOutgoingContext(ctx, "b", strconv.Itoa(B), "u", strconv.Itoa(U))
	} else {
		BValues, BExists := md["b"]
		UValues, UExists := md["u"]

		// methodName := methodNames[0]
		// if no B or U in metadata, this is an entry service
		if !BExists || !UExists {
			// mark this node as entry service
			// d.entryService = true
			// logger("B or U not found. Node %s is assigned as an entry service", d.nodeName)
			if !d.entryService {
				logger("[UnaryInterceptorServer] %s is not a entry service. B or U not found in metadata, fatal error", d.nodeName)
				return nil, status.Errorf(codes.InvalidArgument, "B or U not found in metadata, fatal error")
			}
		}

		// // Assign B based on method from businessMap or metadata
		B, err = strconv.Atoi(BValues[0])
		if err != nil {
			return nil, status.Errorf(codes.InvalidArgument, "Invalid B value: %v", BValues[0])
		}
		logger("[DagorServer] B value provided in metadata: %d", B)

		U, err = strconv.Atoi(UValues[0])
		if err != nil {
			return nil, status.Errorf(codes.InvalidArgument, "Invalid U value: %v", UValues[0])
		}
		logger("[DagorServer] U value provided in metadata: %d", U)
		// }
	}
	// Retrieve current thresholds from admissionLevel
	currentThresholdBVal, _ := d.admissionLevel.Load("B")
	currentThresholdUVal, _ := d.admissionLevel.Load("U")
	currentThresholdB := currentThresholdBVal.(int) // Assert the type to int
	currentThresholdU := currentThresholdUVal.(int) // Assert the type to int

	// If the request's B and U don't meet the threshold, drop the request
	if B < currentThresholdB || (B == currentThresholdB && U <= currentThresholdU) {
		logger("[AQM Server Admit Req] Request B, U %d, %d values are below the threshold %d, %d", B, U, currentThresholdB, currentThresholdU)
		// use go routine to update the histogram d.UpdateHistogram(true, B, U)
		go d.UpdateHistogram(true, B, U)
	} else {
		// if B >= currentThresholdB && U >= currentThresholdU {
		logger("[AQM Server Drop Req] Request B, U %d, %d values are above the threshold %d, %d", B, U, currentThresholdB, currentThresholdU)
		go d.UpdateHistogram(false, B, U)
		return nil, status.Errorf(codes.ResourceExhausted, "[Server Admission Control] Request B, U values do not meet the threshold")
	}

	// Handle the request
	resp, err := handler(ctx, req)
	if err != nil {
		return nil, err
	}

	// Attach B* and U* to the response metadata
	newMD := metadata.Pairs("b-star", strconv.Itoa(currentThresholdB), "u-star", strconv.Itoa(currentThresholdU))
	logger("Attached B*, U* to the response metadata: B*=%d, U*=%d", currentThresholdB, currentThresholdU)
	grpc.SendHeader(ctx, newMD)

	return resp, nil
}

// overloadDetection is a function that detects overload and updates the threshold
func (d *Dagor) UpdateAdmissionLevel() {
	var prevHist *metrics.Float64Histogram
	for range time.Tick(d.admissionLevelUpdateInterval) {
		// get the current histogram
		currHist := readHistogram()

		if prevHist == nil {
			// directly go to next iteration
			prevHist = currHist
			continue
		}
		gapLatency := maximumQueuingDelayms(prevHist, currHist)

		// update the threshold
		foverload := gapLatency > float64(d.queuingThresh.Milliseconds())
		Bstar, Ustar := d.CalculateAdmissionLevel(foverload)

		d.ResetHistogram()

		// Update the admission level with the new values
		// get and update the current threshold values for B and U
		currentThresholdBVal, _ := d.admissionLevel.Swap("B", Bstar)
		currentThresholdUVal, _ := d.admissionLevel.Swap("U", Ustar)

		// If the threshold has changed, log the new values
		if Bstar != currentThresholdBVal.(int) || Ustar != currentThresholdUVal.(int) {
			logger("Updated admission level threshold B, U: %d, %d", Bstar, Ustar)
		}

		// Update prevHist for the next iteration
		prevHist = currHist
	}
}

// Assuming the constants alpha, beta, Bmax, Umax, and the initial N are defined elsewhere
// and the sync.Map C is a part of the Dagor struct initialized appropriately

func (d *Dagor) ResetHistogram() {
	// Reset the N to 0
	d.UpdateN(0)
	d.UpdateNadm(0)
	// Reset the C matrix which holds the admitted request counters
	if d.UseSyncMap {
		d.C.Range(func(key, value interface{}) bool {
			d.C.Store(key, int64(0))
			return true
		})
	} else {
		d.CM.Reset()
	}
	logger("[ResetHistogram] N and C matrix reset")
}

func (d *Dagor) UpdateHistogram(admitted bool, B, U int) {
	// Update the C matrix with the new histogram value
	// increment the counter N
	d.IncrementN()
	logger("[UpdateHistogram] N incremented to %d", d.ReadN())
	if d.UseSyncMap {
		key := [2]int{B, U}
		// This loop ensures that we keep trying to update the value
		// until we are successful in case of concurrent updates
		for {
			// Load the current value
			val, loaded := d.C.Load(key)
			// Store the incremented count using CompareAndSwap
			// This is thread-safe because it only succeeds if the value hasn't been changed by another goroutine in the meantime
			if !loaded {
				// If the key doesn't exist, initialize it to 1
				// Since we are in a loop, we need to check if the initialization was successful
				if d.C.CompareAndSwap(key, nil, int64(1)) {
					logger("[UpdateHistogram] C [%d, %d] (B, U) counter initialized to 1", B, U)
					break
				}
			} else {
				count := val.(int64) + 1
				// Compare and swap the value if it's still the same; otherwise, the loop will retry
				if d.C.CompareAndSwap(key, val, count) {
					logger("[UpdateHistogram] C [%d, %d] (B, U) counter incremented to %d", B, U, count)
					break
				}
			}
		}
	} else {
		// This is an alternative implementation using a map
		d.CM.Increment(B, U)
		logger("[UpdateHistogram] C [%d, %d] (B, U) counter incremented to %d", B, U, d.CM.Get(B, U))
	}

	if admitted {
		d.IncrementNadm()
	}
}

// CalculateAdmissionLevel adjusts the B and U based on the overload flag
func (d *Dagor) CalculateAdmissionLevel(foverload bool) (int, int) {
	Nprefix := d.ReadNadm()
	if Nprefix == 0 {
		logger("[CalculateAdmissionLevel] Nprefix is 0, returning Bmax, Umax")
		return d.Bmax, d.Umax
	}
	// declare Bstar and Ustar
	var Bstar, Ustar int
	// Adjust Nexp based on overload
	if foverload {
		Nexp := int64((1 - d.alpha) * float64(d.ReadNadm()))
		logger("[CalculateAdmissionLevel] overload detected, Nexp updated from %d to %d", d.ReadNadm(), Nexp)
		// while Nprefix > Nexp and (B∗, U∗) > (1, 1)
		Bstar, Ustar = d.Bmax, d.Umax
		for Nprefix > Nexp && (Bstar > 1 || Ustar > 1) {
			Ustar = Ustar - 1
			if Ustar < 1 {
				if Bstar > 1 {
					Bstar = Bstar - 1
					Ustar = d.Umax
				} else {
					Ustar = 1
				}
			}
			if d.UseSyncMap {
				val, _ := d.C.Load([2]int{Bstar, Ustar})
				Nprefix = Nprefix - val.(int64)
			} else {
				Nprefix = Nprefix - d.CM.Get(Bstar, Ustar)
			}
		}
	} else {
		Nexp := d.ReadNadm() + int64(d.beta*float64(d.ReadN())+1) // but take ceiling of the second term
		logger("[CalculateAdmissionLevel] no overload detected, Nexp updated from %d to %d", d.ReadNadm(), Nexp)
		// while Nprefix < Nexp and (B∗, U∗) < (BH , UH ) do
		Bstar, Ustar = 1, 1
		for Nprefix <= Nexp && (Bstar < d.Bmax || Ustar < d.Umax) {
			Ustar = Ustar + 1
			if Ustar > d.Umax {
				if Bstar < d.Bmax {
					Bstar = Bstar + 1
					Ustar = 1
				} else {
					Ustar = d.Umax
				}
			}
			if d.UseSyncMap {
				val, _ := d.C.Load([2]int{Bstar, Ustar})
				Nprefix = Nprefix + val.(int64)
			} else {
				Nprefix = Nprefix + d.CM.Get(Bstar, Ustar)
			}
		}
	}

	return Bstar, Ustar
}
