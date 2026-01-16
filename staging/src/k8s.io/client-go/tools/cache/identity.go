/*
Copyright The Kubernetes Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// Package cache is a client-side caching mechanism. It is useful for
// reducing the number of server calls you'd otherwise need to make.
// Reflector watches a server and updates a Store. Two stores are provided;
// one that simply caches objects (for example, to allow a scheduler to
// list currently available nodes), and one that additionally acts as
// a FIFO queue (for example, to allow a scheduler to process incoming
// pods).
package cache

import (
	"fmt"
	"sync"

	"k8s.io/apimachinery/pkg/runtime/schema"
)

// identifierRegistry tracks all registered identifier keys to detect collisions.
// Keys are composed of name+gvr. Only explicitly named identifiers are registered.
var identifierRegistry = struct {
	sync.Mutex
	keys map[string]bool
}{
	keys: make(map[string]bool),
}

// Identifier is used to identify of informers and FIFO for metrics and logging purposes.
//
// Metrics are only published for identifiers that are:
// 1. Explicitly named (non-empty name)
// 2. Unique (no other identifier has the same name+gvr combination)
//
// This ensures that metrics labels are consistent across restarts and don't collide.
// Unnamed FIFOs will not have metrics published - it's the responsibility of
// client authors to name all FIFOs they care about observing.
type Identifier struct {
	// name is the name used to identify this informer/FIFO.
	name string
	// gvr is the GroupVersionResource of the objects being watched.
	gvr schema.GroupVersionResource
	// unique indicates whether this identifier was successfully registered
	// as unique (true) or if it collided with an existing name+gvr (false).
	unique bool
}

// NewIdentifier creates a new Identifier with the given name and GVR.
// If name and GVR are both non-empty, it will be registered for uniqueness tracking using
// both name and GVR as the composite key.
// If name or GVR is empty, metrics will not be published for this identifier.
// If the name+GVR collides with an existing identifier, IsUnique() will return false,
// metrics will not be published for this identifier, and an error is returned.
func NewIdentifier(name string, gvr schema.GroupVersionResource) (Identifier, error) {
	id := Identifier{name: name, gvr: gvr}
	if name == "" || gvr.Empty() {
		return id, nil
	}
	if err := registerIdentifier(id.name, id.gvr); err != nil {
		return id, err
	}
	id.unique = true
	return id, nil
}

// registerIdentifier attempts to register a name+gvr key and returns nil if the key
// was unique (not previously registered), or an error if it collides.
func registerIdentifier(name string, gvr schema.GroupVersionResource) error {
	key := name + "/" + gvr.String()

	identifierRegistry.Lock()
	defer identifierRegistry.Unlock()

	if identifierRegistry.keys[key] {
		return fmt.Errorf("Identifier %s (gvr=%s) is not unique - metrics will not be published for this identifier", name, gvr.String())
	}

	identifierRegistry.keys[key] = true
	return nil
}

func (id Identifier) Name() string {
	return id.name
}

func (id Identifier) GroupVersionResource() schema.GroupVersionResource {
	return id.gvr
}

// IsUnique returns true if this identifier has an explicit name+gvr that is unique
// across all identifiers. Metrics are only published for unique identifiers.
//
// Returns false if:
// - The identifier has no name (unnamed FIFOs don't get metrics)
// - The identifier's name+gvr collides with another identifier
func (id Identifier) IsUnique() bool {
	return id.unique
}

// ResetRegisteredIdentitiesForTest clears the identifier registry.
// This is exported for testing purposes only.
func ResetRegisteredIdentitiesForTest() {
	identifierRegistry.Lock()
	defer identifierRegistry.Unlock()
	identifierRegistry.keys = make(map[string]bool)
}
