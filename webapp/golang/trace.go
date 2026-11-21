package main

import (
	"context"
	"database/sql"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/jmoiron/sqlx"
)

var traceEnabled = os.Getenv("ISUCONP_TRACE") == "1"

type traceKey struct{}

func traceRequest(next http.Handler) http.Handler {
	if !traceEnabled {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := context.WithValue(r.Context(), traceKey{}, r.Header.Get("X-Request-ID"))
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func traceOperation(ctx context.Context, kind, detail string, start time.Time, err error) {
	if !traceEnabled {
		return
	}
	id, _ := ctx.Value(traceKey{}).(string)
	log.Printf("trace request_id=%q kind=%s duration_ms=%.3f error=%t detail=%q", id, kind, float64(time.Since(start).Microseconds())/1000, err != nil, detail)
}

// These measurements include connection-pool waits, SQL execution and row scanning.
// SQL arguments are deliberately omitted from the log.
type tracedDB struct{ *sqlx.DB }

func (db *tracedDB) GetContext(ctx context.Context, dest interface{}, query string, args ...interface{}) error {
	start := time.Now()
	err := db.DB.GetContext(ctx, dest, query, args...)
	traceOperation(ctx, "sql_get", query, start, err)
	return err
}

func (db *tracedDB) SelectContext(ctx context.Context, dest interface{}, query string, args ...interface{}) error {
	start := time.Now()
	err := db.DB.SelectContext(ctx, dest, query, args...)
	traceOperation(ctx, "sql_select", query, start, err)
	return err
}

func (db *tracedDB) ExecContext(ctx context.Context, query string, args ...interface{}) (sql.Result, error) {
	start := time.Now()
	result, err := db.DB.ExecContext(ctx, query, args...)
	traceOperation(ctx, "sql_exec", query, start, err)
	return result, err
}
