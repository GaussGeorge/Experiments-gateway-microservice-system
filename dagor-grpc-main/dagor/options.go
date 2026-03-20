package dagor

import (
	"math/rand"
	"sync"
	"time"

	"github.com/google/uuid"
)

var debug bool

// Dagor is the DAGOR network.
type Dagor struct {
	nodeName                     string
	uuid                         string         // Only if nodeName is "Client"
	businessMap                  map[string]int // Maps methodName to int B
	queuingThresh                time.Duration  // Overload control in milliseconds
	userPriority                 sync.Map       // Concurrent map from user to priority
	thresholdTable               sync.Map       // Concurrent map to keep B* and U* values for each downstream, key is method name
	entryService                 bool           // Entry service for the DAGOR network
	isEnduser                    bool           // Is this node an end user?
	admissionLevel               sync.Map       // Map to keep track of admission level B and U
	admissionLevelUpdateInterval time.Duration
	alpha                        float64
	beta                         float64
	Umax                         int
	Bmax                         int
	C                            sync.Map
	N                            int64 // Use int64 to be compatible with atomic operations
	Nadm                         int64 // Use int64 to be compatible with atomic operations
	UseSyncMap                   bool
	CM                           *CounterMatrix
	// C is a two-dimensional array or a map that corresponds to the counters for each B, U pair.
	// You need to initialize this with the actual data structure you are using.
}

type thresholdVal struct {
	Bstar int
	Ustar int
}

type DagorParam struct {
	NodeName                     string
	BusinessMap                  map[string]int
	QueuingThresh                time.Duration
	EntryService                 bool
	IsEnduser                    bool
	AdmissionLevelUpdateInterval time.Duration
	Alpha                        float64
	Beta                         float64
	Umax                         int
	Bmax                         int
	Debug                        bool
	UseSyncMap                   bool
}

// NewDagorNode creates a new DAGOR node without a UUID.
func NewDagorNode(params DagorParam) *Dagor {
	dagor := Dagor{
		nodeName:                     params.NodeName,
		uuid:                         uuid.New().String(),
		businessMap:                  params.BusinessMap,
		queuingThresh:                params.QueuingThresh,
		userPriority:                 sync.Map{}, // Initialize as empty concurrent map
		thresholdTable:               sync.Map{}, // Initialize as empty concurrent map
		entryService:                 params.EntryService,
		isEnduser:                    params.IsEnduser,
		admissionLevel:               sync.Map{}, // Initialize as empty concurrent map
		admissionLevelUpdateInterval: params.AdmissionLevelUpdateInterval,
		alpha:                        params.Alpha,
		beta:                         params.Beta,
		Umax:                         params.Umax,
		Bmax:                         params.Bmax,
		UseSyncMap:                   params.UseSyncMap,
		CM:                           NewCounterMatrix(params.Bmax, params.Umax),
	}
	dagor.admissionLevel.Store("B", dagor.Bmax)
	dagor.admissionLevel.Store("U", dagor.Umax)
	rand.Seed(time.Now().UnixNano())

	if dagor.UseSyncMap {
		// Initialize the C matrix with the initial counters for each B, U pair
		for B := 1; B <= params.Bmax; B++ {
			for U := 1; U <= params.Umax; U++ {
				dagor.C.Store([2]int{B, U}, int64(0))
			}
		}
	} else {
		// Initialize the C matrix with the initial counters for each B, U pair
		dagor.CM.Reset()
	}

	if !dagor.isEnduser {
		// go run updateAdmissionLevel(dagor)
		go dagor.UpdateAdmissionLevel()
	}

	// log all the parameters
	logger("Node name: %s", dagor.nodeName)
	logger("UUID: %s", dagor.uuid)
	logger("Business map: %v", dagor.businessMap)
	logger("Queuing threshold: %v", dagor.queuingThresh)
	logger("Entry service: %v", dagor.entryService)
	logger("Is end user: %v", dagor.isEnduser)
	logger("Admission level update interval: %v", dagor.admissionLevelUpdateInterval)
	logger("Alpha: %v", dagor.alpha)
	logger("Beta: %v", dagor.beta)
	logger("Umax: %v", dagor.Umax)
	logger("Bmax: %v", dagor.Bmax)
	debug = params.Debug
	logger("Debug: %v", debug)
	logger("Use sync map: %v", dagor.UseSyncMap)
	return &dagor
}
