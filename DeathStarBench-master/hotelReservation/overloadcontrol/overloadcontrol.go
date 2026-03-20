package overloadcontrol

import (
	"os"
	"strings"
	"time"

	"github.com/pennsail/breakwater-grpc/breakwater"
	"github.com/pennsail/dagor-grpc/dagor"
	"github.com/pennsail/rajomon"
	topdown "github.com/pennsail/topdown-grpc"
	"github.com/rs/zerolog/log"
	"google.golang.org/grpc"
)

// OCType represents the overload control type
type OCType string

const (
	OCNone       OCType = "none"
	OCRajomon    OCType = "rajomon"
	OCDagor      OCType = "dagor"
	OCBreakwater OCType = "breakwater"
	OCTopFull    OCType = "topfull"
)

// GetOCType reads the OC_TYPE environment variable
func GetOCType() OCType {
	t := strings.ToLower(os.Getenv("OC_TYPE"))
	switch t {
	case "rajomon":
		return OCRajomon
	case "dagor":
		return OCDagor
	case "breakwater":
		return OCBreakwater
	case "topfull", "topdown":
		return OCTopFull
	default:
		return OCNone
	}
}

// ServiceOC holds the overload control state for a service
type ServiceOC struct {
	Type       OCType
	Rajomon    *rajomon.PriceTable
	Dagor      *dagor.Dagor
	Breakwater *breakwater.Breakwater
	TopDown    *topdown.TopDownRL
}

// HotelReservation call graph for Rajomon:
//   frontend -> search, reservation, profile, user, recommendation, review, attractions
//   search   -> geo, rate
//   geo      -> (leaf)
//   rate     -> (leaf)
//   profile  -> (leaf)
//   reservation -> (leaf)
//   user     -> (leaf)

// callMaps defines downstream dependencies for each service (Rajomon)
var callMaps = map[string]map[string][]string{
	"frontend": {
		"SearchHotel":  {"srv-search", "srv-reservation", "srv-profile"},
		"ReserveHotel": {"srv-user", "srv-reservation"},
	},
	"search": {
		"Nearby": {"srv-geo", "srv-rate"},
	},
	"geo":           {},
	"rate":          {},
	"profile":       {},
	"reservation":   {},
	"user":          {},
	"recommendation": {},
}

// businessMaps defines business priority values for Dagor
var businessMaps = map[string]map[string]int{
	"frontend":    {"SearchHotel": 5, "ReserveHotel": 8},
	"search":      {"Nearby": 5},
	"geo":         {"Nearby": 5},
	"rate":        {"GetRates": 5},
	"profile":     {"GetProfiles": 5},
	"reservation": {"CheckAvailability": 5, "MakeReservation": 8},
	"user":        {"CheckUser": 5},
}

// sloMaps defines SLO for each method (TopFull)
var sloMaps = map[string]map[string]time.Duration{
	"frontend":    {"SearchHotel": 200 * time.Millisecond, "ReserveHotel": 200 * time.Millisecond},
	"search":      {"Nearby": 100 * time.Millisecond},
	"geo":         {"Nearby": 50 * time.Millisecond},
	"rate":        {"GetRates": 50 * time.Millisecond},
	"profile":     {"GetProfiles": 50 * time.Millisecond},
	"reservation": {"CheckAvailability": 100 * time.Millisecond, "MakeReservation": 100 * time.Millisecond},
	"user":        {"CheckUser": 50 * time.Millisecond},
}

// NewServiceOC creates overload control for a given service
func NewServiceOC(serviceName string, ocType OCType) *ServiceOC {
	soc := &ServiceOC{Type: ocType}

	switch ocType {
	case OCRajomon:
		cm := callMaps[serviceName]
		if cm == nil {
			cm = map[string][]string{}
		}
		options := map[string]interface{}{
			"initprice":        int64(100),
			"rateLimiting":     true,
			"loadShedding":     true,
			"pinpointQueuing":  true,
			"priceUpdateRate":  5 * time.Millisecond,
			"priceStrategy":    "expdecay",
			"priceAggregation": "additive",
			"latencyThreshold": 500 * time.Microsecond,
			"tokenUpdateRate":  100 * time.Microsecond,
			"clientTimeOut":    5 * time.Second,
		}
		soc.Rajomon = rajomon.NewRajomon(serviceName, cm, options)
		log.Info().Msgf("[OC] Rajomon initialized for %s", serviceName)

	case OCDagor:
		bm := businessMaps[serviceName]
		if bm == nil {
			bm = map[string]int{}
		}
		isEntry := serviceName == "frontend" || serviceName == "search"
		params := dagor.DagorParam{
			NodeName:                     serviceName,
			BusinessMap:                  bm,
			QueuingThresh:                10 * time.Millisecond,
			EntryService:                 isEntry,
			IsEnduser:                    false,
			AdmissionLevelUpdateInterval: 50 * time.Millisecond,
			Alpha:                        0.1,
			Beta:                         0.2,
			Umax:                         10,
			Bmax:                         10,
			Debug:                        false,
			UseSyncMap:                   true,
		}
		soc.Dagor = dagor.NewDagorNode(params)
		log.Info().Msgf("[OC] Dagor initialized for %s", serviceName)

	case OCBreakwater:
		params := breakwater.BWParameters{
			ServerSide:              true,
			BFactor:                 0.02,
			AFactor:                 0.001,
			SLO:                     160,
			ClientExpiration:        1000,
			InitialCredits:          1000,
			Verbose:                 false,
			UseClientTimeExpiration: true,
			LoadShedding:            true,
			UseClientQueueLength:    false,
			RTT_MICROSECOND:         5000,
			TrackCredits:            false,
		}
		soc.Breakwater = breakwater.InitBreakwater(params)
		log.Info().Msgf("[OC] Breakwater initialized for %s", serviceName)

	case OCTopFull:
		slo := sloMaps[serviceName]
		if slo == nil {
			slo = map[string]time.Duration{}
		}
		soc.TopDown = topdown.NewTopDownRL(1000, 100, slo, false)
		log.Info().Msgf("[OC] TopFull initialized for %s", serviceName)

	default:
		log.Info().Msgf("[OC] No overload control for %s", serviceName)
	}

	return soc
}

// ServerInterceptor returns the gRPC server interceptor for this OC
func (soc *ServiceOC) ServerInterceptor() grpc.UnaryServerInterceptor {
	switch soc.Type {
	case OCRajomon:
		return soc.Rajomon.UnaryInterceptor
	case OCDagor:
		return soc.Dagor.UnaryInterceptorServer
	case OCBreakwater:
		return soc.Breakwater.UnaryInterceptor
	case OCTopFull:
		return soc.TopDown.UnaryInterceptor
	default:
		return nil
	}
}

// ClientInterceptor returns the gRPC client interceptor for this OC
func (soc *ServiceOC) ClientInterceptor() grpc.UnaryClientInterceptor {
	switch soc.Type {
	case OCRajomon:
		return soc.Rajomon.UnaryInterceptorClient
	case OCDagor:
		return soc.Dagor.UnaryInterceptorClient
	case OCBreakwater:
		return soc.Breakwater.UnaryInterceptorClient
	default:
		return nil
	}
}

// NewClientOC creates a client-side OC (for end-user / load generator)
func NewClientOC(ocType OCType) *ServiceOC {
	soc := &ServiceOC{Type: ocType}

	switch ocType {
	case OCRajomon:
		options := map[string]interface{}{
			"initprice":        int64(100),
			"rateLimiting":     true,
			"loadShedding":     true,
			"pinpointQueuing":  false,
			"priceUpdateRate":  5 * time.Millisecond,
			"priceStrategy":    "expdecay",
			"priceAggregation": "additive",
			"latencyThreshold": 500 * time.Microsecond,
			"tokenUpdateRate":  100 * time.Microsecond,
			"clientTimeOut":    5 * time.Second,
		}
		soc.Rajomon = rajomon.NewRajomon("enduser", map[string][]string{}, options)

	case OCBreakwater:
		params := breakwater.BWParameters{
			ServerSide:              false,
			BFactor:                 0.02,
			AFactor:                 0.001,
			SLO:                     160,
			ClientExpiration:        1000,
			InitialCredits:          1000,
			Verbose:                 false,
			UseClientTimeExpiration: true,
			LoadShedding:            false,
			UseClientQueueLength:    false,
			RTT_MICROSECOND:         5000,
			TrackCredits:            false,
		}
		soc.Breakwater = breakwater.InitBreakwater(params)
	}

	return soc
}

// EnduserClientInterceptor returns the end-user client interceptor
func (soc *ServiceOC) EnduserClientInterceptor() grpc.UnaryClientInterceptor {
	switch soc.Type {
	case OCRajomon:
		return soc.Rajomon.UnaryInterceptorEnduser
	case OCBreakwater:
		return soc.Breakwater.UnaryInterceptorClient
	default:
		return nil
	}
}
