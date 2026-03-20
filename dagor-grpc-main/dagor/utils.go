package dagor

import (
	"fmt"
	"sync/atomic"
	"time"
)

// logger is to mock a sophisticated logging system. To simplify the example, we just print out the content.
func logger(format string, a ...interface{}) {
	if debug {
		// print to stdout with timestamp
		timestamp := time.Now().Format("2006-01-02T15:04:05.999999999-07:00")
		fmt.Printf("LOG: "+timestamp+"|\t"+format+"\n", a...)
	}
}

func (d *Dagor) ReadNadm() int64 {
	return atomic.LoadInt64(&d.Nadm)
}

func (d *Dagor) UpdateNadm(newN int64) {
	atomic.StoreInt64(&d.Nadm, newN)
}

func (d *Dagor) IncrementNadm() {
	atomic.AddInt64(&d.Nadm, 1)
}

func (d *Dagor) DecrementNadm() {
	atomic.AddInt64(&d.Nadm, -1)
}

func (d *Dagor) ReadN() int64 {
	return atomic.LoadInt64(&d.N)
}

func (d *Dagor) UpdateN(newN int64) {
	atomic.StoreInt64(&d.N, newN)
}

func (d *Dagor) IncrementN() {
	atomic.AddInt64(&d.N, 1)
}

func (d *Dagor) DecrementN() {
	atomic.AddInt64(&d.N, -1)
}

// CounterMatrix holds a 2D slice of atomic counters
type CounterMatrix struct {
	Counters [][]*int64
}

// NewCounterMatrix initializes a CounterMatrix with the given dimensions
func NewCounterMatrix(bMax, uMax int) *CounterMatrix {
	matrix := make([][]*int64, bMax)
	for i := range matrix {
		matrix[i] = make([]*int64, uMax)
		for j := range matrix[i] {
			var counter int64
			matrix[i][j] = &counter // Initialize all counters to zero
		}
	}
	return &CounterMatrix{Counters: matrix}
}

// Increment safely increments the counter at the given B and U indices
func (m *CounterMatrix) Increment(B, U int) {
	// Decrement B and U to use as zero-based indices for the slice
	B--
	U--
	atomic.AddInt64(m.Counters[B][U], 1)
}

// Get safely retrieves the current value of the counter at the given B and U indices
func (m *CounterMatrix) Get(B, U int) int64 {
	// Decrement B and U to use as zero-based indices for the slice
	B--
	U--
	return atomic.LoadInt64(m.Counters[B][U])
}

// Reset sets all counters in the matrix to zero
func (m *CounterMatrix) Reset() {
	for i := range m.Counters {
		for j := range m.Counters[i] {
			atomic.StoreInt64(m.Counters[i][j], 0)
		}
	}
}
