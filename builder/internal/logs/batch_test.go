package logs

import (
	"reflect"
	"testing"
	"time"
)

func TestBatchSplitsAtMaxAndFlushesOnClose(t *testing.T) {
	in := make(chan string)
	var got [][]string
	done := make(chan struct{})
	go func() {
		Batch(in, time.Hour, 3, func(b []string) { got = append(got, b) })
		close(done)
	}()
	for _, l := range []string{"1", "2", "3", "4", "5"} {
		in <- l
	}
	close(in)
	<-done
	want := [][]string{{"1", "2", "3"}, {"4", "5"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
}

func TestBatchFlushesOnInterval(t *testing.T) {
	in := make(chan string)
	batches := make(chan []string, 10)
	go Batch(in, 10*time.Millisecond, 200, func(b []string) { batches <- b })
	in <- "a"
	in <- "b"
	select {
	case b := <-batches:
		if !reflect.DeepEqual(b, []string{"a", "b"}) {
			t.Fatalf("got %v", b)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no flush on interval")
	}
	close(in)
}
