#!/bin/bash
#PBS -N Ni_Fe_Engrad_singlet
#PBS -l nodes=1:ppn=12
#PBS -o Ni_Fe_Engrad_singlet.pbs.out
#PBS -e Ni_Fe_Engrad_singlet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Ni_Fe_Engrad_singlet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Ni_Fe_Engrad_singlet.inp > Ni_Fe_Engrad_singlet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
