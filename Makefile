CC      = gcc
CFLAGS  = -std=gnu11 -O2 -g -Wall -Wextra -pthread
LDFLAGS = -pthread

PROGS = pipe_grep uds_grep shm_grep

all: $(PROGS)

pipe_grep: pipe_grep.o common.o
	$(CC) $(LDFLAGS) -o $@ $^

uds_grep: uds_grep.o common.o
	$(CC) $(LDFLAGS) -o $@ $^

shm_grep: shm_grep.o common.o
	$(CC) $(LDFLAGS) -o $@ $^ -lrt

%.o: %.c common.h
	$(CC) $(CFLAGS) -c -o $@ $<

test: all
	python3 tests/run_tests.py

clean:
	rm -f $(PROGS) *.o

.PHONY: all test clean
