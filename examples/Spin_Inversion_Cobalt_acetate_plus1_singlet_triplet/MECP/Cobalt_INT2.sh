#!/bin/bash
#PBS -N Cobalt_INT2
#PBS -l nodes=1:ppn=12
#PBS -o Cobalt_INT2.pbs.out
#PBS -e Cobalt_INT2.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Cobalt_INT2"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Cobalt_INT2.inp > Cobalt_INT2.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
