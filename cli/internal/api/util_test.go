package api

import "fmt"

func sprintf(f string, a ...any) string { return fmt.Sprintf(f, a...) }
