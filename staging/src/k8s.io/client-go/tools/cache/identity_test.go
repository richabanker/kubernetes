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
	"testing"

	"k8s.io/apimachinery/pkg/runtime/schema"
)

var (
	podsGVR       = schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}
	configMapsGVR = schema.GroupVersionResource{Group: "", Version: "v1", Resource: "configmaps"}
)

func TestIdentifierUniqueness(t *testing.T) {
	tests := []struct {
		name       string
		setup      func() // create identifiers before the test
		idName     string
		gvr        schema.GroupVersionResource
		wantUnique bool
		wantErr    bool
	}{
		{
			name:       "first identifier with name is unique",
			setup:      func() {},
			idName:     "my-fifo",
			gvr:        podsGVR,
			wantUnique: true,
			wantErr:    false,
		},
		{
			name: "same name different gvr is unique",
			setup: func() {
				_, _ = NewIdentifier("my-fifo", podsGVR)
			},
			idName:     "my-fifo",
			gvr:        configMapsGVR,
			wantUnique: true,
			wantErr:    false,
		},
		{
			name: "different name same gvr is unique",
			setup: func() {
				_, _ = NewIdentifier("fifo-1", podsGVR)
			},
			idName:     "fifo-2",
			gvr:        podsGVR,
			wantUnique: true,
			wantErr:    false,
		},
		{
			name: "duplicate name+gvr returns error",
			setup: func() {
				_, _ = NewIdentifier("my-fifo", podsGVR)
			},
			idName:     "my-fifo",
			gvr:        podsGVR,
			wantUnique: false,
			wantErr:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ResetRegisteredIdentitiesForTest()
			tt.setup()

			id, err := NewIdentifier(tt.idName, tt.gvr)

			if (err != nil) != tt.wantErr {
				t.Errorf("NewIdentifier() error = %v, wantErr %v", err, tt.wantErr)
			}
			if got := id.IsUnique(); got != tt.wantUnique {
				t.Errorf("IsUnique() = %v, want %v", got, tt.wantUnique)
			}
		})
	}
}
