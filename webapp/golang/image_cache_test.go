package main

import "testing"

func TestImageCacheEvictionAndClear(t *testing.T) {
	c := newImageCache(516)
	c.put(cachedImage{id: 1, mime: "image/png", data: []byte("ab")})
	c.put(cachedImage{id: 2, data: []byte("cd")})
	if got, ok := c.get(1); !ok || got.mime != "image/png" || string(got.data) != "ab" {
		t.Fatalf("unexpected cached image: %+v, %v", got, ok)
	}
	c.put(cachedImage{id: 3, data: []byte("ef")})
	if _, ok := c.get(2); ok {
		t.Fatal("least recently used image was not evicted")
	}
	c.clear()
	if _, ok := c.get(1); ok {
		t.Fatal("clear left an image in the cache")
	}
	c.put(cachedImage{id: 4, data: make([]byte, 261)})
	if _, ok := c.get(4); ok {
		t.Fatal("oversized image should not be cached")
	}
}
