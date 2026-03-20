package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"
)

// Result stores the result of a single request
type Result struct {
	RequestType string        `json:"request_type"`
	StartTime   time.Time     `json:"start_time"`
	Latency     time.Duration `json:"latency"`
	Success     bool          `json:"success"`
	Error       string        `json:"error,omitempty"`
}

var (
	frontendAddr = flag.String("frontend", "localhost:5000", "Frontend HTTP address")
	searchAddr   = flag.String("search", "localhost:8082", "Search service gRPC address")
	reserveAddr  = flag.String("reserve", "localhost:8087", "Reservation service gRPC address")
	targetRPS    = flag.Int("rps", 4000, "Target requests per second (total)")
	duration     = flag.Duration("duration", 10*time.Second, "Test duration")
	warmupDur    = flag.Duration("warmup", 5*time.Second, "Warmup duration")
	warmupRPS    = flag.Int("warmup-rps", 0, "Warmup RPS (default: 80% of rps)")
	workers      = flag.Int("workers", 1000, "Number of concurrent workers")
	outputFile   = flag.String("output", "results.csv", "Output CSV file")
	searchRatio  = flag.Float64("search-ratio", 0.5, "Ratio of search requests vs reserve")
	sloMs        = flag.Float64("slo", 200, "SLO in milliseconds")
	mode         = flag.String("mode", "http", "Mode: http (frontend) or grpc (direct)")
)

func main() {
	flag.Parse()

	if *warmupRPS == 0 {
		*warmupRPS = int(float64(*targetRPS) * 0.8)
	}

	fmt.Printf("=== Benchmark Configuration ===\n")
	fmt.Printf("Mode:         %s\n", *mode)
	fmt.Printf("Target RPS:   %d\n", *targetRPS)
	fmt.Printf("Warmup RPS:   %d\n", *warmupRPS)
	fmt.Printf("Duration:     %v\n", *duration)
	fmt.Printf("Warmup:       %v\n", *warmupDur)
	fmt.Printf("Workers:      %d\n", *workers)
	fmt.Printf("Search Ratio: %.2f\n", *searchRatio)
	fmt.Printf("SLO:          %.0f ms\n", *sloMs)
	fmt.Printf("Output:       %s\n", *outputFile)
	fmt.Println()

	var results []Result
	var mu sync.Mutex

	slo := time.Duration(*sloMs) * time.Millisecond

	// Phase 1: Warmup
	fmt.Println("--- Warmup Phase ---")
	warmupResults := runPhase(*warmupRPS, *warmupDur, *workers, *searchRatio, *mode, *frontendAddr)
	fmt.Printf("Warmup complete: %d requests\n\n", len(warmupResults))

	// Phase 2: Overload
	fmt.Println("--- Overload Phase ---")
	overloadResults := runPhase(*targetRPS, *duration, *workers, *searchRatio, *mode, *frontendAddr)

	mu.Lock()
	results = append(results, overloadResults...)
	mu.Unlock()

	// Compute metrics
	computeAndPrintMetrics(results, slo)

	// Write results to CSV
	writeCSV(*outputFile, results)
	fmt.Printf("\nResults written to %s\n", *outputFile)
}

func runPhase(rps int, dur time.Duration, numWorkers int, searchRatio float64, mode, addr string) []Result {
	var results []Result
	var mu sync.Mutex
	var sent int64

	interval := time.Second / time.Duration(rps)
	deadline := time.Now().Add(dur)

	sem := make(chan struct{}, numWorkers)
	var wg sync.WaitGroup

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for time.Now().Before(deadline) {
		<-ticker.C
		atomic.AddInt64(&sent, 1)

		sem <- struct{}{}
		wg.Add(1)

		isSearch := rand.Float64() < searchRatio
		go func() {
			defer wg.Done()
			defer func() { <-sem }()

			var result Result
			start := time.Now()

			if isSearch {
				result.RequestType = "SearchHotel"
				err := doSearchRequest(mode, addr)
				result.Latency = time.Since(start)
				result.Success = err == nil
				if err != nil {
					result.Error = err.Error()
				}
			} else {
				result.RequestType = "ReserveHotel"
				err := doReserveRequest(mode, addr)
				result.Latency = time.Since(start)
				result.Success = err == nil
				if err != nil {
					result.Error = err.Error()
				}
			}
			result.StartTime = start

			mu.Lock()
			results = append(results, result)
			mu.Unlock()
		}()
	}

	wg.Wait()
	return results
}

func doSearchRequest(mode, addr string) error {
	if mode == "http" {
		return doHTTPSearch(addr)
	}
	return doGRPCSearch(addr)
}

func doReserveRequest(mode, addr string) error {
	if mode == "http" {
		return doHTTPReserve(addr)
	}
	return doGRPCReserve(addr)
}

func doHTTPSearch(addr string) error {
	lat := 37.7749 + rand.Float64()*0.1 - 0.05
	lon := -122.4194 + rand.Float64()*0.1 - 0.05

	url := fmt.Sprintf("http://%s/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=%f&lon=%f",
		addr, lat, lon)

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	return nil
}

func doHTTPReserve(addr string) error {
	hotelId := strconv.Itoa(rand.Intn(80) + 1)
	username := fmt.Sprintf("Cornell_%d", rand.Intn(500))
	password := fmt.Sprintf("%d_Cornell", rand.Intn(500))

	url := fmt.Sprintf("http://%s/reservation?inDate=2015-04-09&outDate=2015-04-10&hotelId=%s&customerName=test&username=%s&password=%s&number=1",
		addr, hotelId, username, password)

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	return nil
}

func doGRPCSearch(_ string) error {
	// Placeholder for direct gRPC calls
	return fmt.Errorf("direct gRPC mode not yet implemented - use http mode")
}

func doGRPCReserve(_ string) error {
	return fmt.Errorf("direct gRPC mode not yet implemented - use http mode")
}

func computeAndPrintMetrics(results []Result, slo time.Duration) {
	searchResults := filterByType(results, "SearchHotel")
	reserveResults := filterByType(results, "ReserveHotel")

	fmt.Println("\n=== Results ===")
	printMetricsForType("SearchHotel", searchResults, slo)
	printMetricsForType("ReserveHotel", reserveResults, slo)
	printMetricsForType("Total", results, slo)
}

func printMetricsForType(name string, results []Result, slo time.Duration) {
	if len(results) == 0 {
		fmt.Printf("\n[%s] No requests\n", name)
		return
	}

	var latencies []time.Duration
	goodput := 0
	for _, r := range results {
		latencies = append(latencies, r.Latency)
		if r.Success && r.Latency <= slo {
			goodput++
		}
	}

	// Sort latencies
	sortDurations(latencies)

	total := len(results)
	p50 := latencies[int(float64(total)*0.50)]
	p95 := latencies[int(float64(total)*0.95)]
	p99 := latencies[int(float64(total)*0.99)]

	// Calculate duration for RPS
	minTime := results[0].StartTime
	maxTime := results[0].StartTime
	for _, r := range results {
		if r.StartTime.Before(minTime) {
			minTime = r.StartTime
		}
		if r.StartTime.After(maxTime) {
			maxTime = r.StartTime
		}
	}
	dur := maxTime.Sub(minTime)
	if dur == 0 {
		dur = time.Second
	}

	rps := float64(total) / dur.Seconds()
	goodputRPS := float64(goodput) / dur.Seconds()

	fmt.Printf("\n[%s]\n", name)
	fmt.Printf("  Total Requests:     %d\n", total)
	fmt.Printf("  Throughput:         %.1f RPS\n", rps)
	fmt.Printf("  Goodput:            %.1f RPS (within SLO)\n", goodputRPS)
	fmt.Printf("  P50 Latency:        %v\n", p50)
	fmt.Printf("  P95 Latency:        %v\n", p95)
	fmt.Printf("  P99 Latency:        %v\n", p99)
	fmt.Printf("  SLO Violations:     %d (%.1f%%)\n",
		total-goodput, float64(total-goodput)/float64(total)*100)
}

func filterByType(results []Result, reqType string) []Result {
	var filtered []Result
	for _, r := range results {
		if r.RequestType == reqType {
			filtered = append(filtered, r)
		}
	}
	return filtered
}

func sortDurations(d []time.Duration) {
	for i := 1; i < len(d); i++ {
		for j := i; j > 0 && d[j] < d[j-1]; j-- {
			d[j], d[j-1] = d[j-1], d[j]
		}
	}
}

func writeCSV(filename string, results []Result) {
	f, err := os.Create(filename)
	if err != nil {
		fmt.Printf("Error creating file: %v\n", err)
		return
	}
	defer f.Close()

	w := csv.NewWriter(f)
	defer w.Flush()

	w.Write([]string{"request_type", "start_time_ns", "latency_ms", "success", "error"})

	for _, r := range results {
		w.Write([]string{
			r.RequestType,
			strconv.FormatInt(r.StartTime.UnixNano(), 10),
			fmt.Sprintf("%.3f", float64(r.Latency.Microseconds())/1000.0),
			strconv.FormatBool(r.Success),
			r.Error,
		})
	}
}

// Unused import guard
var _ = json.Marshal
var _ grpc.ClientConnInterface
var _ = insecure.NewCredentials
var _ = keepalive.ClientParameters{}
var _ = context.Background
