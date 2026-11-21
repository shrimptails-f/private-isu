package main

import (
	"context"
	"testing"
)

func TestPasswordHashCompatibility(t *testing.T) {
	ctx := context.Background()
	if got := calculateSalt(ctx, "alice"); got != "408b27d3097eea5a46bf2ab6433a7234a33d5e49957b13ec7acc2ca08e1a13c75272c90c8d3385d47ede5420a7a9623aad817d9f8a70bd100a0acea7400daa59" {
		t.Fatalf("salt = %q", got)
	}
	if got := calculatePasshash(ctx, "alice", "password123"); got != "6bc8113bb2daca1af10cea613dae7a4e3255e9c769d4666388815efd0c801f76fcc53aec24fb5275a4ad79ca33a767eae035d9a07d5ac7d869736a6360393792" {
		t.Fatalf("passhash = %q", got)
	}
}
