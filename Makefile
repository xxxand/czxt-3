CC      = gcc
CFLAGS  = -std=gnu11 -O2 -g -Wall -Wextra -pthread
LDFLAGS = -pthread

BUILD_DIR = build
PROGS = $(BUILD_DIR)/pipe_grep $(BUILD_DIR)/uds_grep $(BUILD_DIR)/shm_grep

all: $(PROGS)

$(BUILD_DIR)/pipe_grep: $(BUILD_DIR)/pipe_grep.o $(BUILD_DIR)/common.o
	$(CC) $(LDFLAGS) -o $@ $^

$(BUILD_DIR)/uds_grep: $(BUILD_DIR)/uds_grep.o $(BUILD_DIR)/common.o
	$(CC) $(LDFLAGS) -o $@ $^

$(BUILD_DIR)/shm_grep: $(BUILD_DIR)/shm_grep.o $(BUILD_DIR)/common.o
	$(CC) $(LDFLAGS) -o $@ $^ -lrt

$(BUILD_DIR)/%.o: %.c common.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD_DIR):
	mkdir -p $@

test: all
	python3 tests/run_tests.py

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all test clean
