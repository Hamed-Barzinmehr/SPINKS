#!/bin/bash
#PBS -N Ni_Fe_singlet_min
#PBS -l nodes=1:ppn=12
#PBS -o Ni_Fe_singlet_min.pbs.out
#PBS -e Ni_Fe_singlet_min.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Ni_Fe_singlet_min"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Ni_Fe_singlet_min.inp > Ni_Fe_singlet_min.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
