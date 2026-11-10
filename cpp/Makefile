CXX ?= clang++
CXXFLAGS = -O3 -march=native -std=c++17 -ffp-contract=off -fno-fast-math -Wall -Wextra -Wno-unused-parameter
rq: rq.cpp
	$(CXX) $(CXXFLAGS) -o rq rq.cpp
clean-build:
	$(CXX) $(CXXFLAGS) -o rq rq.cpp
