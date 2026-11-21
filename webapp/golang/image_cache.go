package main

import (
	"container/list"
	"sync"
)

// imageCache retains at most maxBytes of image data. The application clears it
// when /initialize changes the database; it is empty after an app restart.
type imageCache struct {
	mu       sync.Mutex
	maxBytes int
	bytes    int
	items    map[int]*list.Element
	lru      list.List
}

type cachedImage struct {
	id   int
	mime string
	data []byte
}

func imageCost(image cachedImage) int { return len(image.data) + 256 }

func newImageCache(maxBytes int) *imageCache {
	return &imageCache{maxBytes: maxBytes, items: make(map[int]*list.Element)}
}

func (c *imageCache) get(id int) (cachedImage, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	item, ok := c.items[id]
	if !ok {
		return cachedImage{}, false
	}
	c.lru.MoveToFront(item)
	return item.Value.(cachedImage), true
}

func (c *imageCache) put(image cachedImage) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if old, ok := c.items[image.id]; ok {
		c.bytes -= imageCost(old.Value.(cachedImage))
		c.lru.Remove(old)
		delete(c.items, image.id)
	}
	if imageCost(image) > c.maxBytes {
		return
	}
	for c.bytes+imageCost(image) > c.maxBytes {
		old := c.lru.Back()
		entry := old.Value.(cachedImage)
		c.bytes -= imageCost(entry)
		delete(c.items, entry.id)
		c.lru.Remove(old)
	}
	c.items[image.id] = c.lru.PushFront(image)
	c.bytes += imageCost(image)
}

func (c *imageCache) clear() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.items = make(map[int]*list.Element)
	c.lru.Init()
	c.bytes = 0
}
