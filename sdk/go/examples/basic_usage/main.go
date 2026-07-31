package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	openviking "github.com/volcengine/OpenViking/sdk/go"
)

const (
	baseURL = "http://localhost:1940"
	apiKey  = "" // Set this when your OpenViking server requires authentication.
)

func main() {
	ctx := context.Background()

	client, err := openviking.NewClient(openviking.Config{
		BaseURL: baseURL,
		APIKey:  apiKey,
		Timeout: 120 * time.Second,
	})
	if err != nil {
		log.Fatal(err)
	}
	defer client.CloseIdleConnections()

	fmt.Println("1. Health check")
	healthy, err := client.Health(ctx)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   healthy=%v\n", healthy)

	tmpFile := createDemoFile()
	defer os.RemoveAll(filepath.Dir(tmpFile))

	resourceRoot := fmt.Sprintf(
		"viking://resources/go-sdk-smoke/%d",
		time.Now().Unix(),
	)
	resourceURI := resourceRoot + "/demo.md"

	fmt.Println("2. Add local resource")
	resource, err := client.AddResource(ctx, tmpFile, &openviking.AddResourceOptions{
		To:     resourceRoot,
		Reason: "Go SDK smoke test",
		Wait:   true,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   resource=%v\n", resource)

	fmt.Println("3. Wait for background processing")
	waitResult, err := client.WaitProcessed(ctx, &openviking.WaitProcessedOptions{
		Timeout: openviking.Float64(120),
	})
	if err != nil {
		fmt.Printf("   wait warning: %v\n", err)
	} else {
		fmt.Printf("   wait=%v\n", waitResult)
	}

	fmt.Println("4. List and read the resource")
	entries, err := client.List(ctx, resourceRoot, &openviking.ListOptions{
		Recursive: true,
		Simple:    true,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   entries=%v\n", entries)

	content, err := client.Read(ctx, resourceURI, 0, -1)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   read=%q\n", content)

	fmt.Println("5. Write and find")
	_, err = client.Write(ctx, resourceURI, content+"\n\nGo SDK write check.", &openviking.WriteOptions{
		Mode: "replace",
		Wait: true,
	})
	if err != nil {
		log.Fatal(err)
	}

	findResult, err := client.Find(ctx, "Go SDK smoke test", &openviking.FindOptions{
		TargetURI: "viking://resources/go-sdk-smoke",
		Limit:     5,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   find resources=%d memories=%d skills=%d\n",
		len(findResult.Resources),
		len(findResult.Memories),
		len(findResult.Skills),
	)
	for _, item := range findResult.Resources {
		fmt.Printf("   - %s score=%.3f\n", item.URI, item.Score)
	}

	watchRoot := resourceRoot + "-watch"
	fmt.Println("6. Watch management")
	watchResource, err := client.AddResource(ctx, tmpFile, &openviking.AddResourceOptions{
		To:            watchRoot,
		Reason:        "Go SDK watch smoke test",
		Wait:          true,
		WatchInterval: 60,
	})
	if err != nil {
		fmt.Printf("   watch create skipped: %v\n", err)
	} else {
		fmt.Printf("   watch resource=%v\n", watchResource)
		defer func() {
			if _, err := client.DeleteWatch(context.Background(), openviking.WatchRef{ToURI: watchRoot}); err != nil {
				fmt.Printf("   watch cleanup warning: %v\n", err)
			}
		}()

		watches, err := client.ListWatches(ctx, &openviking.ListWatchesOptions{
			ActiveOnly: true,
			ToURI:      watchRoot,
		})
		if err != nil {
			log.Fatal(err)
		}
		fmt.Printf("   watch=%v\n", watches)

		paused, err := client.UpdateWatch(ctx, openviking.UpdateWatchOptions{
			ToURI:    watchRoot,
			IsActive: openviking.Bool(false),
			Reason:   openviking.String("Paused by Go SDK smoke test"),
		})
		if err != nil {
			log.Fatal(err)
		}
		fmt.Printf("   paused=%v\n", paused)

		resumed, err := client.UpdateWatch(ctx, openviking.UpdateWatchOptions{
			ToURI:         watchRoot,
			IsActive:      openviking.Bool(true),
			WatchInterval: openviking.Float64(60),
			Instruction:   openviking.String("Refresh this resource for Go SDK smoke validation."),
		})
		if err != nil {
			log.Fatal(err)
		}
		fmt.Printf("   resumed=%v\n", resumed)

		triggered, err := client.TriggerWatch(ctx, openviking.WatchRef{ToURI: watchRoot})
		if err != nil {
			log.Fatal(err)
		}
		fmt.Printf("   triggered=%v\n", triggered)
	}

	fmt.Println("7. Skill management")
	skillName := fmt.Sprintf("go-sdk-smoke-%d", time.Now().Unix())
	skillDir := createDemoSkill(skillName, "Validate the OpenViking Go SDK smoke flow.")
	defer os.RemoveAll(skillDir)

	validation, err := client.ValidateSkill(ctx, map[string]any{
		"name":        skillName,
		"description": "Validate the OpenViking Go SDK smoke flow.",
		"content":     "# " + skillName + "\n\nUse this skill to validate the Go SDK smoke flow.",
	}, &openviking.ValidateSkillOptions{
		Strict:       true,
		SkillDirName: skillName,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   validation=%v\n", validation)

	skill, err := client.AddSkill(ctx, skillDir, &openviking.AddSkillOptions{
		Wait:    true,
		Timeout: openviking.Float64(120),
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   skill=%v\n", skill)
	defer func() {
		if _, err := client.DeleteSkill(context.Background(), skillName); err != nil {
			fmt.Printf("   skill cleanup warning: %v\n", err)
		}
	}()

	skills, err := client.ListSkills(ctx, nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   skills total=%v\n", skills["total"])

	gotSkill, err := client.GetSkill(ctx, skillName, &openviking.GetSkillOptions{
		IncludeContent: openviking.Bool(true),
		IncludeFiles:   openviking.Bool(true),
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   got skill name=%v files=%v\n", gotSkill["name"], gotSkill["files"])

	updateDemoSkill(skillDir, skillName, "Updated by Go SDK smoke test.")
	updatedSkill, err := client.UpdateSkill(ctx, skillName, skillDir, &openviking.UpdateSkillOptions{
		Wait:    true,
		Timeout: openviking.Float64(120),
		SourceMetadata: map[string]any{
			"type":      "example",
			"operation": "basic_usage",
		},
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   updated skill=%v\n", updatedSkill)

	foundSkills, err := client.FindSkills(ctx, "Go SDK smoke validation", &openviking.FindSkillsOptions{
		Limit: 5,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   found skills total=%v\n", foundSkills["total"])

	fmt.Println("8. Session message and commit")
	sessionID := fmt.Sprintf("go-sdk-smoke-%d", time.Now().Unix())
	memoryMarker := "go-sdk-memory-marker-" + sessionID
	peerID := "go-sdk-peer-" + sessionID
	peerMarker := "go-sdk-peer-marker-" + sessionID
	session, err := client.CreateSession(ctx, &openviking.CreateSessionOptions{
		SessionID: sessionID,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   session=%v\n", session)

	messages := []openviking.Message{
		{
			Role:    "user",
			Content: openviking.String("Please remember this durable Go SDK validation marker: " + memoryMarker + "."),
		},
		{
			Role:    "assistant",
			Content: openviking.String("Understood. I will remember that the Go SDK validation marker is " + memoryMarker + "."),
		},
		{
			Role: "user",
			Content: openviking.String(
				"My OpenViking Go SDK preference is that examples should exercise resources, watches, skills, session commits, and memory retrieval without requiring Account or User constants.",
			),
		},
		{
			Role: "assistant",
			Content: openviking.String(
				"I will treat that as a durable preference for the OpenViking Go SDK example workflow.",
			),
		},
		{
			Role:   "user",
			PeerID: peerID,
			Content: openviking.String(
				"I am the peer for this smoke test. Please remember my peer marker " + peerMarker + " and that I care about peer-scoped memory retrieval.",
			),
		},
		{
			Role: "assistant",
			Content: openviking.String(
				"I will remember that peer " + peerID + " uses marker " + peerMarker + " for peer-scoped Go SDK validation.",
			),
		},
	}
	batch, err := client.BatchAddMessages(ctx, sessionID, messages, nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   batch messages=%v\n", batch)

	sessionContext, err := client.GetSessionContext(ctx, sessionID, 4096)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   context=%v\n", sessionContext)

	commit, err := client.CommitSession(ctx, sessionID, &openviking.CommitSessionOptions{
		KeepRecentCount: 0,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   commit=%v\n", commit)

	if taskID, _ := commit["task_id"].(string); taskID != "" {
		task, err := waitForTask(ctx, client, taskID, 3*time.Minute)
		if err != nil {
			log.Fatal(err)
		}
		fmt.Printf("   commit task=%v\n", task)
	} else {
		fmt.Println("   commit did not create a background task; no messages were archived.")
	}
	tasks, err := client.ListTasks(ctx, &openviking.ListTasksOptions{
		TaskType:   "session_commit",
		ResourceID: sessionID,
		Limit:      5,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   recent session commit tasks=%d\n", len(tasks))

	memoryResults, err := client.Find(ctx, memoryMarker, &openviking.FindOptions{
		TargetURI:   "viking://user/memories",
		Limit:       5,
		ContextType: []string{"memory"},
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   memory hits=%d\n", len(memoryResults.Memories))
	for _, item := range memoryResults.Memories {
		fmt.Printf("   - memory %s score=%.3f\n", item.URI, item.Score)
	}
	if len(memoryResults.Memories) == 0 {
		fmt.Printf("   memory retrieval warning: no memory hit for marker %q\n", memoryMarker)
	}

	peerClient, err := openviking.NewClient(openviking.Config{
		BaseURL:     baseURL,
		APIKey:      apiKey,
		ActorPeerID: peerID,
		Timeout:     120 * time.Second,
	})
	if err != nil {
		log.Fatal(err)
	}
	defer peerClient.CloseIdleConnections()

	peerMemoryResults, err := peerClient.Find(ctx, peerMarker, &openviking.FindOptions{
		TargetURI:   "viking://user/memories",
		Limit:       5,
		ContextType: []string{"memory"},
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("   peer memory hits=%d peer_id=%s\n", len(peerMemoryResults.Memories), peerID)
	for _, item := range peerMemoryResults.Memories {
		fmt.Printf("   - peer memory %s score=%.3f\n", item.URI, item.Score)
	}
	if len(peerMemoryResults.Memories) == 0 {
		fmt.Printf("   peer memory retrieval warning: no memory hit for marker %q\n", peerMarker)
	}

	fmt.Println("Go SDK smoke test completed.")
}

func createDemoFile() string {
	dir, err := os.MkdirTemp("", "openviking-go-sdk-smoke-*")
	if err != nil {
		log.Fatal(err)
	}
	path := filepath.Join(dir, "demo.md")
	content := `# OpenViking Go SDK Smoke Test

This file was created by sdk/go/examples/basic_usage.

It verifies resource, watch, skill, retrieval, session commit, memory retrieval, and peer-scoped memory APIs.
`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		log.Fatal(err)
	}
	return path
}

func createDemoSkill(name string, description string) string {
	dir, err := os.MkdirTemp("", name+"-*")
	if err != nil {
		log.Fatal(err)
	}
	writeDemoSkill(dir, name, description)
	return dir
}

func updateDemoSkill(dir string, name string, description string) {
	writeDemoSkill(dir, name, description)
}

func writeDemoSkill(dir string, name string, description string) {
	content := fmt.Sprintf(`---
name: %s
description: %s
---

# %s

Use this temporary skill to validate OpenViking Go SDK skill management.

## When To Use

Use it only while running sdk/go/examples/basic_usage.
`, name, description, name)
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte(content), 0o644); err != nil {
		log.Fatal(err)
	}
}

func waitForTask(ctx context.Context, client *openviking.Client, taskID string, timeout time.Duration) (map[string]any, error) {
	deadline := time.Now().Add(timeout)
	for {
		task, err := client.GetTask(ctx, taskID)
		if err != nil {
			return nil, err
		}
		if task != nil {
			status := fmt.Sprint(task["status"])
			stage := fmt.Sprint(task["stage"])
			if status == "completed" || stage == "completed" {
				return task, nil
			}
			if status == "failed" || status == "error" || stage == "failed" || stage == "error" {
				return task, fmt.Errorf("task %s ended with status=%s stage=%s", taskID, status, stage)
			}
		}
		if time.Now().After(deadline) {
			return task, fmt.Errorf("timed out waiting for task %s", taskID)
		}
		time.Sleep(2 * time.Second)
	}
}
